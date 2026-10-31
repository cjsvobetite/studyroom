"""Report data (analysis.build) -> one self-contained HTML file in Korean. Print it to PDF from the browser (A4)."""
import base64
import io
from html import escape

import numpy as np
from PIL import Image

STATUS = {"good": "#0ca30c", "borderline": "#fab219", "ungradable": "#6b6a64"}   # no red: red marks expert input
ICON = {"good": "●", "borderline": "▲", "ungradable": "✕"}


def _e(x):
    return escape("" if x is None else str(x))


def _num(x, suffix=""):
    return "—" if x is None else f"{x}{suffix}"


def _ci(c, short=False):
    if not c or c["value"] is None:
        return "—"
    if short:
        return f"{c['value']}%<br><span class=muted>{c['low']}–{c['high']}</span>"
    return f"{c['value']}% <span class=muted>(95% CI {c['low']}–{c['high']})</span>"


def _table(head, rows, cls=""):
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
    return f'<div class=tw><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def _ex(value, what):
    """Red: a specialist enters or confirms it. Shown in red when given, as a red placeholder when not."""
    if value not in (None, ""):
        return f"<span class=ex>{_e(value)}</span>"
    return f'<span class="ex ph">[전문의 입력] {_e(what)}</span>'


def _llm(value, what, inputs, block=False):
    """Blue: an LLM can draft it from the inputs named; a specialist reviews the draft."""
    tag = "div" if block else "span"
    if value not in (None, ""):
        return f"<{tag} class=llm>{_e(value)}</{tag}>"
    return (f'<{tag} class="llm ph"><b>[LLM 작성]</b> {_e(what)}'
            + (f' <span class=src>· 입력: {_e(inputs)}</span>' if inputs else "") + f"</{tag}>")


# --------------------------------------------------------------------------------------------- gallery

def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _overlay(gray, mask, colors, alpha=0.45):
    rgb = np.stack([gray] * 3, axis=-1).astype(np.float32)
    if mask is not None:
        for v, col in colors.items():
            m = mask == v
            if m.any():
                rgb[m] = rgb[m] * (1 - alpha) + np.array(col, np.float32) * alpha
    return Image.fromarray(rgb.clip(0, 255).astype(np.uint8))


def gallery(data, primary, adjudicated=None, ai=None, limit=6):
    """Error cases as (original | confirmed label | AI) strips, base64 JPEG."""
    errs = data.get("errors")
    if not errs or ai is None or limit <= 0:
        return []
    colors = {lab["value"]: _hex(lab["color"]) for lab in data["labels"]}
    picks = [("FN", x) for x in errs["fn"][: (limit + 1) // 2]] + [("FP", x) for x in errs["fp"][: limit // 2]]
    out = []
    for kind, x in picks:
        case = primary.cases.get(x["name"])
        img = primary.image(case)
        if img is None:
            continue
        final = None
        if adjudicated is not None and (c3 := adjudicated.find(case)):
            final = adjudicated.mask(c3, img.shape)
        if final is None:
            final = primary.mask(case, img.shape)
        pred = ai.mask(ai.find(case), img.shape)
        tiles = [Image.fromarray(img).convert("RGB"), _overlay(img, final, colors), _overlay(img, pred, colors)]
        h = 150
        tiles = [t.resize((max(1, round(t.width * h / t.height)), h), Image.BILINEAR) for t in tiles]
        strip = Image.new("RGB", (sum(t.width for t in tiles) + 8 * (len(tiles) - 1), h), (255, 255, 255))
        xo = 0
        for t in tiles:
            strip.paste(t, (xo, 0))
            xo += t.width + 8
        buf = io.BytesIO()
        strip.save(buf, "JPEG", quality=82)
        cause = (data["errors"]["fn_table"] if kind == "FN" else data["errors"]["fp_table"])
        cause = next((c["cause"] for c in cause if c["key"] == x["cause"]), x["cause"])
        out.append({"name": x["name"], "kind": kind, "cause": cause + (f" — {x['overlap']}" if x.get("overlap") else ""),
                    "review": x.get("review", ""), "src": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()})
    return out


# ---------------------------------------------------------------------------------------------- charts

def _qc_bar(qc):
    n = qc["n"] or 1
    segs, x = [], 0.0
    for c in qc["classes"]:
        w = 100 * c["n"] / n
        if w > 0:
            segs.append(f'<div class=seg style="width:calc({w:.3f}% - 2px);background:{STATUS[c["key"]]}" '
                        f'title="{_e(c["name"])}: {c["n"]}장 ({c["pct"]}%)"></div>')
        x += w
    legend = "".join(f'<span class=lg><i style="color:{STATUS[c["key"]]}">{ICON[c["key"]]}</i>{_e(c["name"])} '
                     f'<b>{c["pct"]}%</b></span>' for c in qc["classes"])
    return f'<div class=bar role=img aria-label="화질 분포">{"".join(segs)}</div><div class=legend>{legend}</div>'


def _forest(subgroups, overall):
    rows = [s for s in subgroups if s["sensitivity"]["value"] is not None]
    if not rows:
        return ""
    lo = min(min(s["sensitivity"]["low"] for s in rows), overall or 100) - 5
    lo = max(0, int(lo // 10 * 10))
    W, L, R, rh = 640, 170, 20, 30
    H = rh * len(rows) + 40
    sx = lambda v: L + (v - lo) / (100 - lo) * (W - L - R)  # noqa: E731
    out = [f'<svg viewBox="0 0 {W} {H}" class=forest role=img aria-label="서브그룹 민감도와 95% 신뢰구간">']
    for t in range(lo, 101, 10):
        out.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="8" y2="{H - 24}" class=grid />'
                   f'<text x="{sx(t):.1f}" y="{H - 8}" class=tick text-anchor=middle>{t}%</text>')
    if overall is not None:
        out.append(f'<line x1="{sx(overall):.1f}" x2="{sx(overall):.1f}" y1="4" y2="{H - 24}" class=ref />'
                   f'<text x="{sx(overall) + 4:.1f}" y="12" class=tick>전체 {overall}%</text>')
    for i, s in enumerate(rows):
        y = 26 + i * rh
        c = s["sensitivity"]
        cls = "flag" if s["flag"] else "dot"
        out.append(f'<g><title>{_e(s["group"])} {_e(s["value"])}: 민감도 {c["value"]}% (95% CI {c["low"]}–{c["high"]}), '
                   f'양성 {s["n_pos"]}건</title>'
                   f'<rect x="0" y="{y - rh / 2}" width="{W}" height="{rh}" fill="transparent" />'
                   f'<text x="{L - 10}" y="{y + 4}" class=lab text-anchor=end>{_e(s["group"])} · {_e(s["value"])}</text>'
                   f'<line x1="{sx(c["low"]):.1f}" x2="{sx(c["high"]):.1f}" y1="{y}" y2="{y}" class="whisk {cls}" />'
                   f'<circle cx="{sx(c["value"]):.1f}" cy="{y}" r="5" class="{cls}" />'
                   + (f'<text x="{sx(c["high"]) + 6:.1f}" y="{y + 4}" class="tick flagt">▼ 저하</text>' if s["flag"] else "")
                   + "</g>")
    out.append("</svg>")
    return "".join(out)


# ----------------------------------------------------------------------------------------------- page

CSS = """
:root{--bg:#f6f5f1;--paper:#fff;--ink:#1b1b1a;--ink2:#4a4a46;--muted:#7a7a73;--line:#e1e0d9;--axis:#c3c2b7;
--accent:#1f5fbf;--accent-bg:#eef3fb;--warn-bg:#fff6e0;--crit:#d03b3b;--good:#0ca30c;--dot:#4a4a46;--drop:#c4561f;--gray-bg:#f2f1ec}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121211;--paper:#1a1a19;--ink:#ecebe6;--ink2:#c8c7c0;
--muted:#9a9990;--line:#2c2c2a;--axis:#383835;--accent:#7aa7ef;--accent-bg:#1c2533;--warn-bg:#2e2716;--crit:#ef6b6b;--dot:#c8c7c0;--drop:#ec835a;--gray-bg:#232321}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Pretendard","Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",system-ui,sans-serif}
.page{max-width:880px;margin:24px auto;background:var(--paper);padding:40px 48px;border:1px solid var(--line);border-radius:8px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px;padding-top:12px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:20px 0 8px}p{margin:6px 0}.muted{color:var(--muted);font-size:12px}
.tw{overflow-x:auto;margin:8px 0}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--ink2);font-weight:600;background:var(--gray-bg)}
td.num,th.num{text-align:right}.cover td:first-child{width:140px;color:var(--ink2);font-weight:600}
.callout{border-radius:6px;padding:12px 16px;margin:10px 0}.callout.info{background:var(--accent-bg)}.callout.warn{background:var(--warn-bg)}
.callout.gray{background:var(--gray-bg);font-size:13px}.callout ul{margin:0;padding-left:18px}
.drop{color:var(--drop);font-weight:600}.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:9px;background:var(--gray-bg);color:var(--ink2)}
.bar{display:flex;gap:2px;height:22px;margin:10px 0 6px}.seg{border-radius:4px;min-width:3px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2)}.lg i{font-style:normal;margin-right:4px}
svg.forest{width:100%;height:auto;font-size:12px}.forest .grid{stroke:var(--line)}.forest .ref{stroke:var(--axis);stroke-dasharray:4 3}
.forest .tick{fill:var(--muted);font-size:11px}.forest .lab{fill:var(--ink2)}.forest .whisk{stroke-width:2}.forest .whisk.dot{stroke:var(--dot)}
.forest .whisk.flag{stroke:var(--drop)}.forest circle.dot{fill:var(--dot);stroke:var(--paper);stroke-width:2}
.forest circle.flag{fill:var(--drop);stroke:var(--paper);stroke-width:2}.forest .flagt{fill:var(--drop)}
.cm td,.cm th{text-align:center}.nw td:first-child{white-space:nowrap}.gal{display:grid;gap:14px}.gal figure{margin:0}.gal img{max-width:100%;border-radius:4px;border:1px solid var(--line)}
.gal figcaption{font-size:12px;color:var(--ink2)}.legend-sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
ol.rec li{margin:4px 0}.ex{color:var(--crit)}.llm{color:var(--accent)}
.ph{border:1px dashed currentColor;border-radius:4px;padding:1px 6px;font-size:12.5px;display:inline-block;line-height:1.5}
div.ph{display:block;padding:8px 12px;margin:8px 0}.ph .src{opacity:.8}
.key{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;border:1px solid var(--line);border-radius:6px;padding:10px 14px;margin:10px 0}
.key b{font-weight:600}.sign td{height:34px}.hdr{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
@media (max-width:640px){.page{margin:0;padding:20px 16px;border:0;border-radius:0}}
@media print{body{background:#fff}.page{margin:0;border:0;padding:0;max-width:none}h2,h3{break-after:avoid}tr{break-inside:avoid}table,figure,.callout{break-inside:avoid}
@page{size:A4;margin:16mm 14mm}}
"""


def render(data, gallery_items=()):
    cfg = data["config"]
    rp = cfg["report"]
    src = data["source"]
    out = []
    w = out.append
    title = rp.get("title") or "어노테이션 예비 검증 보고서"
    w(f'<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
      f'<title>{_e(title)} {_e(rp["number"])}</title><style>{CSS}</style></head><body><main class=page>')
    w(f'<div class=hdr><h1>{_e(title)}</h1><span class=muted>{_e(rp["number"])} ({_e(rp["version"])}) · '
      f'생성 {_e(data.get("generated_at", ""))}</span></div>')
    if rp.get("demo"):
        w('<div class="callout warn">⚠️ 본 문서는 <b>보고서 형식 시연용 견본</b>이며, 모든 수치와 사례는 가상 데이터로 자동 생성되었습니다.</div>')
    w('<div class=key><span><b>검정</b> 플랫폼 export에서 자동 산출</span><span class=ex><b>빨강</b> 전문의가 직접 입력·확인</span>'
      '<span class=llm><b>파랑</b> LLM을 연결하면 초안 작성 (전문의 검토 후 확정)</span></div>')
    ex, llm = cfg.get("expert") or {}, cfg.get("llm") or {}
    cov = llm.get("cover") or {}
    for x in data["warnings"][:10]:
        w(f'<div class="callout warn">⚠️ {_e(x)}</div>')

    # cover
    w("<h2>표지</h2>")
    w(_table(["항목", "내용"], [
        ["보고서 번호", f"{_e(rp['number'])} ({_e(rp['version'])})"],
        ["의뢰 기관", _e(rp["client"]) or _llm(cov.get("client"), "의뢰 기관명", "의뢰서")],
        ["대상 AI", _e(rp["target_ai"]) or _llm(cov.get("target_ai"), "대상 AI 제품명·원 학습 장비", "의뢰서")],
        ["검증 목적", _e(rp["purpose"]) or _llm(cov.get("purpose"), "검증 목적 한 문장", "의뢰서, 계약 범위")],
        ["데이터", _e(rp["data_desc"] or f"{src['primary']['project']} — {data['qc']['n']:,}장 "
                                       f"({src['primary']['modality'] or '영상'})")],
        ["수행 기간", _e(rp["period"]) or _llm(cov.get("period"), "수행 기간", "계약서, 플랫폼 이벤트 로그")],
        ["수행 기관", _e(rp["org"])], ["판독 책임자", _ex(rp["responsible"], "판독 책임자 성명·자격")],
        ["원자료", f"{_e(src['primary']['path'])} <span class=muted>(export {_e(src['primary']['exported_at'])}, "
                f"{_e(src['primary']['format'])})</span>"]], "cover"))

    # 1 summary
    w('<h2>1. 요약 (Executive Summary)</h2><div class="callout gray"><ul>')
    w("".join(f"<li>{s}</li>" for s in data["summary"]))
    w("</ul></div>")
    w(_llm(llm.get("summary"), "종합 해석 문단 (임상적 의미, 본 검증 진행 여부에 대한 제언)",
           "report.json 집계값, 위 요약 항목", True))

    # 2 scope and methods
    ep = data["endpoint"]
    w("<h2>2. 범위와 방법</h2><h3>2.1 판독 기준</h3><ul>")
    labs = " ".join(f'<span class=badge><span class=legend-sw style="background:{_e(lab["color"])}"></span>{_e(lab["name"])}</span>'
                    for lab in data["labels"])
    w(f"<li>라벨 체계: {labs}</li>")
    w(f"<li>엔드포인트 <b>{_e(ep['name'])}</b>: {', '.join(_e(x) for x in ep['labels'])} 중 하나 이상 존재</li>")
    w(f"<li>병변 '있음' 기준: 해당 라벨 마스크 {cfg['min_lesion_px']}px 이상 · 판독자 간 불일치: 병변 유무 불일치 또는 "
      f"엔드포인트 Dice &lt; {cfg['disagreement_dice']}</li>")
    w("<li>판독 불가 기준: 신호 약함(저대비), 노출 과다(포화), 초점 불량(흐림) — 판독자가 화질을 지정한 영상은 그 값을 우선</li>")
    w(f'<li>라벨별 판독 정의(SOP): {_ex(ex.get("sop"), "라벨별 판독 정의, 엔드포인트의 임상적 정의, 판독 불가 판정 기준")}</li></ul>')
    w("<h3>2.2 판독 절차</h3>")
    pr = data["procedure"]
    w(_table(["단계", "내용", "대상"], [[_e(s["step"]), _e(s["desc"]),
                                       f"{s['n']:,}" + ("건" if s["step"] == "불일치 조정" else "장")
                                       + (f" / 불일치 {s['of']}건" if "of" in s else "")] for s in pr["steps"]]))
    w(f'<ul><li>맹검 여부: {_ex(ex.get("blinding"), "판독 시 AI 결과·타 판독자 결과 공개 여부, 판독 환경")}</li>'
      f"<li>AI 보조 도구(점·윤곽 프롬프트)는 영역 제안에만 사용했으며 ({pr['assisted']:,}장, {_num(pr['assisted_pct'], '%')}), "
      "마스크는 판독자가 최종 확정했습니다.</li></ul>")

    # 3 QC
    qc = data["qc"]
    w("<h2>3. 영상 QC 결과</h2>")
    w(_qc_bar(qc))
    w(_table(["분류", "장수", "비율"], [[f'<span style="color:{STATUS[c["key"]]}">{ICON[c["key"]]}</span> {_e(c["name"])}',
                                      f"{c['n']:,}", f"{c['pct']}%"] for c in qc["classes"]]
             + [["<b>합계</b>", f"<b>{qc['n']:,}</b>", "<b>100%</b>"]]))
    if qc["ungradable_reasons"]:
        w("<p>판독 불가 사유: " + ", ".join(f"{_e(k)} {v}" for k, v in qc["ungradable_reasons"]) + "</p>")
    w(f'<p class=muted>분류 근거: 판독자 지정 {qc["source"]["reader"]}건, 자동 화질 지표 {qc["source"]["auto"]}건</p>')
    if qc["source"]["auto"]:
        n_auto = qc["source"]["auto"]
        w(f'<p>화질 확인: {_ex(ex.get("qc_check"), f"자동 화질 분류 {n_auto}건 확인 (판독 불가·경계 우선)")}</p>')

    # 4 agreement
    agr = data.get("agreement")
    w(f"<h2>4. 판독자 간 일치도 (R1 vs R2, n={agr['n'] if agr else 0})</h2>")
    if agr:
        e = agr["rows"][0]
        w(_table(["지표", _e(e["name"])], [
            ["완전 일치율", f"{e['agree_pct']}% ({e['agree']}/{e['n']})"], ["Kappa (Cohen)", _num(e["kappa"])],
            ["평균 Dice", f"{_num(e['dice'])} <span class=muted>(n={e['n_dice']})</span>"],
            ["불일치 건수 (유무 또는 Dice 기준)", str(agr["n_disagree"])]]))
        shown = [r for r in agr["rows"][1:] if r["pos_r1"] or r["pos_r2"]]
        w(_table(["라벨", "R1 양성", "R2 양성", "일치율", "κ", "평균 Dice (n)"],
                 [[_e(r["name"]), r["pos_r1"], r["pos_r2"], f"{r['agree_pct']}%", _num(r["kappa"]),
                   f"{_num(r['dice'])} ({r['n_dice']})"] for r in shown]))
        if len(shown) < len(agr["rows"]) - 1:
            w(f'<p class=muted>두 판독자 모두 양성이 없는 라벨 {len(agr["rows"]) - 1 - len(shown)}개는 생략했습니다.</p>')
        w(f"<p>→ 불일치 {agr['n_disagree']}건 중 {pr['n_adjudicated']}건은 R3 조정으로 확정했으며, 이력은 부록 A에 기록했습니다.</p>")
    else:
        w('<p class=muted>제2 판독 자료(--second)가 없어 생략했습니다.</p>')

    # 5 AI
    ai = data.get("ai")
    w(f"<h2>5. AI 성능 (기준: 확정 라벨, n={ai['n'] if ai else 0})</h2>")
    if ai:
        cm = ai["cm"]
        w(_table(["", f"확정 양성 ({ai['n_pos']})", f"확정 음성 ({ai['n_neg']})"],
                 [["<b>AI 양성</b>", f"{cm['tp']} (TP)", f"{cm['fp']} (FP)"],
                  ["<b>AI 음성</b>", f"{cm['fn']} (FN)", f"{cm['tn']} (TN)"]], "cm"))
        bl = ai.get("baseline") or {}
        head = ["지표", "본 검증"] + ([_e(bl.get("label", "기준값"))] if bl else [])
        rows = [["민감도", _ci(ai["sensitivity"])], ["특이도", _ci(ai["specificity"])],
                ["평균 Dice (TP)", _num(ai["dice"])]]
        if bl:
            rows[0].append(_num(round(100 * bl["sensitivity"], 1) if bl.get("sensitivity") is not None else None, "%"))
            rows[1].append(_num(round(100 * bl["specificity"], 1) if bl.get("specificity") is not None else None, "%"))
            rows[2].append("—")
        else:
            head.append("기준값 (원 학습 장비)")
            for r in rows[:2]:
                r.append(_llm((llm.get("baseline") or {}).get(r[0]), f"의뢰사 제공 {r[0]}", "의뢰사 성능 자료"))
            rows[2].append("—")
        w(_table(head, rows))
        w("<h3>라벨별 성능</h3>")
        shown = [r for r in ai["per_label"] if r["n_pos"] or r["fp"]]
        w(_table(["라벨", "확정 양성", "TP", "FN", "FP", "민감도 (95% CI)", "특이도 (95% CI)", "평균 Dice"],
                 [[_e(r["name"]), r["n_pos"], r["tp"], r["fn"], r["fp"], _ci(r["sensitivity"], True),
                   _ci(r["specificity"], True), _num(r["dice"])] for r in shown]))
        if len(shown) < len(ai["per_label"]):
            w(f'<p class=muted>확정 양성과 AI 양성이 모두 없는 라벨 {len(ai["per_label"]) - len(shown)}개는 생략했습니다.</p>')
    else:
        w('<p class=muted>AI 예측 자료(--ai)가 없어 생략했습니다.</p>')

    # 6 subgroups
    if ai and data["subgroups"]:
        w("<h2>6. 서브그룹 분석 (민감도)</h2>")
        w(_forest(data["subgroups"], ai["sensitivity"]["value"]))
        w(_table(["서브그룹", "영상 수", "양성", "TP", "민감도", "특이도"],
                 [[f"{_e(s['group'])} · {_e(s['value'])}", s["n"], s["n_pos"], s["tp"],
                   (f'<span class=drop>▼ {s["sensitivity"]["value"]}%</span>' if s["flag"] else _num(s["sensitivity"]["value"], "%")),
                   _num(s["specificity"]["value"], "%")] for s in data["subgroups"]]))
        w(f'<div class="callout gray">ℹ️ ▼ 표시는 전체 민감도보다 {round(100 * cfg["flag_drop"])}%p 이상 낮은 구간입니다. '
          "서브그룹 표본 수가 작아 신뢰구간이 넓습니다. 본 결과는 경향 확인용이며 통계적 결론으로 해석하지 않습니다.</div>")

    # 7 errors
    errs = data.get("errors")
    if errs:
        w("<h2>7. 오류 분석</h2>")
        w(f"<h3>위음성 (FN, {len(errs['fn'])}건)</h3>")
        fo, po = ex.get("fn_opinion") or {}, ex.get("fp_opinion") or {}
        w(_table(["원인 (자동 분류)", "건수", "예시", "판독자 소견"],
                 [[_e(t["cause"]), t["n"], _e(", ".join(t["examples"])), _ex(fo.get(t["key"]), "원인에 대한 임상 소견")]
                  for t in errs["fn_table"]]))
        w(f"<h3>위양성 (FP, {len(errs['fp'])}건)</h3>")
        w(_table(["원인 (자동 분류)", "건수", "세부", "판독자 소견"],
                 [[_e(t["cause"]), t["n"], _e(", ".join(f"{k} {v}" for k, v in t["detail"]) or "—"),
                   _ex(po.get(t["key"]), "원인에 대한 임상 소견")] for t in errs["fp_table"]]))
        w(_llm(llm.get("errors"), "오류 경향 서술 (원인별 패턴, 장비·화질과의 관계)",
               "원인 분류표, 사례별 Clinical Description(플랫폼 입력), 판독자 소견", True))
        if gallery_items:
            w("<h3>오류 사례</h3><p class=muted>각 사례: 원본 | 확정 라벨 | AI 예측 (라벨 색은 2.1 참조)</p><div class=gal>")
            for g in gallery_items:
                w(f'<figure><img src="{g["src"]}" alt="{_e(g["name"])} {g["kind"]}"><figcaption><b>{g["kind"]}</b> '
                  f'{_e(g["name"])} — {_e(g["cause"])}<br>판독자 소견: ' + _ex(g["review"], "사례 소견 (플랫폼 Review 노트)")
                  + "</figcaption></figure>")
            w("</div>")

    # 8, 9
    w('<h2>8. 권고사항</h2><ol class=rec>')
    w("".join(f"<li><b>{_e(r['title'])}</b>: {_e(r['text'])}</li>" for r in data["recommendations"]))
    w("</ol>")
    w(_llm(llm.get("recommendations"), "권고별 근거와 실행 방법 서술", "규칙 기반 권고 목록, 서브그룹·오류 분석 결과", True))
    w(f'<p>판독 책임자 검토 의견: {_ex(ex.get("recommendations"), "권고 추가·삭제·수정 및 최종 승인")}</p>')
    w("<h2>9. 해석 범위와 한계</h2><ul>")
    w("".join(f"<li>{x}</li>" for x in data["limitations"]))
    w("</ul>")

    # appendices
    if agr:
        w("<h2>부록 A. 불일치 조정 이력</h2>")
        w(_table(["영상 ID", "R1", "R2", "확정", "Dice", "조정 근거"],
                 [[_e(d["name"]), _e(d["r1"]), _e(d["r2"]),
                   _e(d["final"]) + ("" if d["final_source"] == "R3" else ' <span class=badge>R1 유지</span>'),
                   _num(d["dice"]), _ex(d["review"], "조정 근거")] for d in agr["disagreements"]], "nw"))
    w("<h2>부록 B. 판독자 정보</h2>")
    w(_table(["ID", "역할", "자격", "SOP 교육 이수", "골드스탠다드 일치도 (κ)", "판독 수", "확정 라벨 대비 κ"],
             [[_e(r["id"]), r["role"], _ex(r["qualification"], "자격"), _ex(r["sop"], "이수일"),
               "기준 판독자" if r["role"] == "R3" and r["gold_kappa"] == "기준 판독자"
               else _ex(None if r["gold_kappa"] is None else str(r["gold_kappa"]), "κ"), r["n"], _num(r["kappa_final"])]
              for r in data["readers"]]))
    if not any(r["role"] == "R3" for r in data["readers"]) and data["procedure"]["n_adjudicated"]:
        w(f'<p>R3 (조정 판독자): {_ex(None, "ID·자격")}</p>')
    h = data["handling"]
    w("<h2>부록 C. 데이터 처리</h2><ul>")
    w(f"<li>영상 무결성: SHA-256 기록 {data['qc']['n']:,}장 · 목록 해시 <code>{h['manifest_sha256'][:16]}…</code></li>")
    w(f"<li>중복 점검: 동일 파일 {len(h['exact_duplicates'])}쌍, 유사 중복(미세 구조 상관 ≥ {cfg['near_duplicate_corr']}) "
      f"{len(h['near_duplicates'])}쌍" + (" — " + _e(", ".join(f"{a}↔{b}" for a, b, _ in h["near_duplicates"][:5]))
                                        if h["near_duplicates"] else "") + "</li>")
    w("".join(f"<li>{_e(x)}</li>" for x in h["notes"]) or
      "<li>" + _llm(llm.get("data_handling"), "비식별 확인·접근 권한·원본 파기 조건", "계약서, 데이터 처리 SOP") + "</li>")
    w("<li>납품물: 본 보고서(HTML/PDF), 라벨 원자료(platform export zip: masks/, points/, lines/, annotations.json), "
      "영상별 결과(cases.csv), 집계 원자료(report.json)</li></ul>")
    w("<h2>확인 및 서명</h2>")
    w(_table(["역할", "성명", "서명", "일자"],
             [[r, _ex(None, "성명"), _ex(None, "서명"), _ex(None, "일자")] for r in ("판독 책임자 (R3)", "QC 담당자")], "sign"))
    w("</main></body></html>")
    return "".join(out)
