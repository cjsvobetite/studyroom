"""Builds the report data from the exports. Everything the HTML shows is in the returned dict (report.json).

Readers and roles follow the pre-validation report layout:
  R1  primary export: every case, one reader (the platform's annotator)
  R2  optional second-reader export of a sample (matched by image name / sha256)
  R3  optional adjudicated export: the confirmed label for the cases it contains (R1 otherwise)
  AI  optional prediction set (export layout or masks/ only) compared with the confirmed labels

Lesion presence per label = at least `min_lesion_px` pixels of that label. The endpoint (e.g. "exudative fluid")
is positive when any of its labels is present; agreement and AI performance are reported for the endpoint and per
label.
"""
import copy
import csv
import hashlib
import json
import os

import numpy as np

from . import metrics as M

DEFAULT_CONFIG = {
    "report": {
        "number": "RTN-PV-0000-000", "version": "v1.0", "title": "어노테이션 예비 검증 보고서",
        "client": "", "target_ai": "", "purpose": "", "data_desc": "", "period": "", "org": "", "responsible": "",
        "demo": False,
    },
    "endpoint": {"name": "병변 있음 (any lesion)", "labels": []},   # [] = every label
    "min_lesion_px": 30,
    "small_lesion_px": 400,          # error analysis: endpoint area below this is a "small lesion"
    "disagreement_dice": 0.5,        # R1 / R2 pairs below this Dice (endpoint) count as a disagreement
    "near_duplicate_corr": 0.99,      # fine-structure correlation for near-duplicate images
    "quality": {"field": "image_quality", "thresholds": {}},
    "subgroups": [],                 # [{"column": "age", "name": "연령", "bins": [0, 65, 200], "labels": [...]}]
    "baseline": None,                # {"label": "...", "sensitivity": 0.93, "specificity": 0.91}
    "flag_drop": 0.05,               # subgroup sensitivity this far below overall is flagged
    "readers": {},                   # username -> {"role", "qualification", "sop", "gold_kappa"}
    "data_handling": [],
    "gallery": 6,
}

QUALITY_KO = {"good": "양호", "borderline": "경계 (판독 가능)", "ungradable": "판독 불가"}
QUALITY_ALIASES = {"good": "good", "양호": "good", "borderline": "borderline", "경계": "borderline",
                   "ungradable": "ungradable", "unreadable": "ungradable", "판독 불가": "ungradable",
                   "판독불가": "ungradable"}


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def read_metadata(path):
    """CSV with a `name` (image file name) or `stem` column -> {key: row}."""
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        key = r.get("name") or r.get("image") or r.get("stem")
        if key:
            out[key] = r
            out[os.path.splitext(key)[0]] = r
    return out


def _pct(x, d=1):
    return None if x is None else round(100 * x, d)


def _ci(t):
    p, lo, hi = t
    return {"value": _pct(p), "low": _pct(lo), "high": _pct(hi)}


def _subgroup_key(sg, row):
    raw = (row or {}).get(sg["column"], "")
    if raw in ("", None):
        return None
    if sg.get("bins"):
        try:
            x = float(raw)
        except ValueError:
            return None
        b = sg["bins"]
        names = sg.get("labels") or [f"{b[i]}–{b[i + 1]}" for i in range(len(b) - 1)]
        for i in range(len(b) - 1):
            if b[i] <= x < b[i + 1]:
                return names[i]
        return None
    return str(raw)


def build(primary, second=None, adjudicated=None, ai=None, metadata=None, config=None):
    cfg = merge(DEFAULT_CONFIG, config)
    metadata = metadata or {}
    labels = primary.labels
    names = {lab["value"]: lab["name"] for lab in labels}
    ep_names = cfg["endpoint"]["labels"] or [lab["name"] for lab in labels]
    ep_values = [lab["value"] for lab in labels if lab["name"] in ep_names]
    unknown = sorted(set(ep_names) - set(names.values()))
    warnings = [f"endpoint label '{n}' is not in the export's labels" for n in unknown]
    min_px = cfg["min_lesion_px"]
    qfield = cfg["quality"]["field"]

    def presence(mask):
        counts = np.bincount(mask.ravel(), minlength=256)
        return {lab["name"]: bool(counts[lab["value"]] >= min_px) for lab in labels}

    def ep(mask):
        return np.isin(mask, ep_values)

    rows, hashes = [], {}
    for case in sorted(primary.cases.values(), key=lambda c: c.name):
        img = primary.image(case)
        r1 = primary.mask(case)
        shape = r1.shape if r1 is not None else (img.shape if img is not None else None)
        if r1 is None:
            r1 = np.zeros(shape, np.uint8) if shape else None
        if r1 is None:
            warnings.append(f"{case.name}: neither mask nor image, skipped")
            continue
        q_reader = QUALITY_ALIASES.get(str(case.texts.get(qfield, "")).strip().lower())
        if img is not None:
            q_auto, reasons, measures = M.image_quality(img, cfg["quality"]["thresholds"])
            hashes[case.name] = M.fingerprint(img)
        else:
            q_auto, reasons, measures = None, [], {}
        quality = q_reader or q_auto or "good"
        if q_reader and q_reader != "good" and not reasons:
            reasons = ["판독자 지정"]
        row = {"name": case.name, "stem": case.stem, "sha256": case.sha256, "annotator": case.annotator,
               "quality": quality, "quality_source": "reader" if q_reader else ("auto" if q_auto else "none"),
               "quality_auto": q_auto, "quality_reasons": reasons if quality != "good" else [], "qc": measures,
               "prompts": primary.prompt_count(case), "review": (case.review or {}).get("text") or "",
               "meta": metadata.get(case.name) or metadata.get(case.stem) or {}}
        row["r1"] = presence(r1)
        row["r1_ep"] = any(row["r1"][n] for n in ep_names if n in row["r1"])
        c2 = second.find(case) if second else None
        r2 = second.mask(c2, r1.shape) if c2 else None
        if r2 is not None and r2.shape != r1.shape:
            warnings.append(f"{case.name}: R2 mask size differs, R2 ignored")
            r2 = None
        c3 = adjudicated.find(case) if adjudicated else None
        r3 = adjudicated.mask(c3, r1.shape) if c3 else None
        if r3 is not None and r3.shape != r1.shape:
            warnings.append(f"{case.name}: adjudicated mask size differs, ignored")
            r3 = None
        final = r3 if r3 is not None else r1
        row["final_source"] = "R3" if r3 is not None else "R1"
        if r2 is not None:
            row["r2_annotator"] = c2.annotator
            row["r2"] = presence(r2)
            row["r2_ep"] = any(row["r2"][n] for n in ep_names if n in row["r2"])
            row["r1r2_dice"] = {lab["name"]: M.dice(r1 == lab["value"], r2 == lab["value"]) for lab in labels}
            row["r1r2_ep_dice"] = M.dice(ep(r1), ep(r2))
            mism = [n for n in row["r1"] if row["r1"][n] != row["r2"][n]]
            low = row["r1r2_ep_dice"] is not None and row["r1r2_ep_dice"] < cfg["disagreement_dice"]
            row["disagree"] = bool(mism or low)
            row["disagree_labels"] = mism
            if c3 is not None and c3.review.get("text"):
                row["review"] = c3.review["text"]
        row["final"] = presence(final)
        row["final_ep"] = any(row["final"][n] for n in ep_names if n in row["final"])
        row["final_ep_px"] = int(ep(final).sum())
        row["final_ep_edge"] = M.edge_touch(ep(final))
        if ai is not None:
            ca = ai.find(case)
            pa = ai.mask(ca, r1.shape) if ca else None
            if pa is not None and pa.shape != r1.shape:
                warnings.append(f"{case.name}: AI mask size differs, AI ignored")
                pa = None
            if pa is not None:
                row["ai"] = presence(pa)
                row["ai_ep"] = any(row["ai"][n] for n in ep_names if n in row["ai"])
                row["ai_ep_px"] = int(ep(pa).sum())
                row["ai_dice"] = {lab["name"]: M.dice(final == lab["value"], pa == lab["value"]) for lab in labels}
                row["ai_ep_dice"] = M.dice(ep(final), ep(pa))
                # what the confirmed label says where the AI predicted the endpoint but it is not there
                fp_px = ep(pa) & ~ep(final)
                other = np.bincount(final[fp_px], minlength=256) if fp_px.any() else np.zeros(256, int)
                other[0] = 0
                for v in ep_values:
                    other[v] = 0
                row["ai_fp_overlap"] = names.get(int(other.argmax())) if other.max() > 0.3 * max(fp_px.sum(), 1) else None
        rows.append(row)

    data = {"config": cfg, "warnings": warnings, "labels": labels, "endpoint": {"name": cfg["endpoint"]["name"],
                                                                                "labels": ep_names}}
    data["source"] = {
        "primary": {"path": os.path.basename(primary.path), "project": primary.project, "modality": primary.modality,
                    "exported_at": primary.exported_at, "format": primary.format, "cases": len(primary.cases)},
        "second": second and {"path": os.path.basename(second.path), "project": second.project,
                              "cases": len(second.cases)},
        "adjudicated": adjudicated and {"path": os.path.basename(adjudicated.path), "cases": len(adjudicated.cases)},
        "ai": ai and {"path": os.path.basename(ai.path), "cases": len(ai.cases)},
    }
    data["cases"] = rows
    _qc(data, rows)
    gradable = [r for r in rows if r["quality"] != "ungradable"]
    _procedure(data, rows, gradable)
    _agreement(data, gradable, labels, ep_names)
    _ai(data, gradable, labels, cfg)
    _subgroups(data, gradable, cfg)
    _errors(data, gradable, cfg)
    _readers(data, gradable, cfg)
    _handling(data, rows, hashes, cfg)
    _recommend(data, cfg)
    _limits(data, gradable)
    _summary(data)
    return data


def _qc(data, rows):
    n = len(rows)
    by = {k: sum(r["quality"] == k for r in rows) for k in QUALITY_KO}
    reasons = {}
    for r in rows:
        if r["quality"] == "ungradable":
            for x in r["quality_reasons"] or ["사유 미기재"]:
                reasons[x] = reasons.get(x, 0) + 1
    src = {s: sum(r["quality_source"] == s for r in rows) for s in ("reader", "auto", "none")}
    data["qc"] = {"n": n, "classes": [{"key": k, "name": QUALITY_KO[k], "n": by[k], "pct": _pct(by[k] / n if n else 0)}
                                      for k in QUALITY_KO],
                  "ungradable_reasons": sorted(reasons.items(), key=lambda kv: -kv[1]), "source": src}


def _procedure(data, rows, gradable):
    r1_readers = sorted({r["annotator"] for r in rows if r["annotator"]})
    paired = [r for r in gradable if "r2" in r]
    r2_readers = sorted({r.get("r2_annotator") for r in paired if r.get("r2_annotator")})
    dis = [r for r in paired if r["disagree"]]
    adj = [r for r in dis if r["final_source"] == "R3"]
    with_ai = [r for r in gradable if "ai" in r]
    assisted = sum(r["prompts"] > 0 for r in gradable)
    data["procedure"] = {
        "steps": [
            {"step": "영상 QC", "desc": "비식별 확인, 화질 3단계(양호/경계/불가) 분류 — 자동 화질 지표 + 판독자 지정", "n": len(rows)},
            {"step": "1차 판독", "desc": f"R1 전수 판독 ({', '.join(r1_readers) or '판독자 미기재'})", "n": len(gradable)},
            {"step": "2차 판독", "desc": f"R2 독립 판독 ({', '.join(r2_readers) or '—'}), 표본 "
                                        f"{_pct(len(paired) / len(gradable)) if gradable else 0}%", "n": len(paired)},
            {"step": "불일치 조정", "desc": "R3가 R1·R2 불일치 건을 판정", "n": len(adj), "of": len(dis)},
            {"step": "AI 비교", "desc": "확정 라벨과 의뢰 AI 결과 비교", "n": len(with_ai)},
        ],
        "assisted": assisted, "assisted_pct": _pct(assisted / len(gradable)) if gradable else None,
        "n_gradable": len(gradable), "n_paired": len(paired), "n_disagree": len(dis), "n_adjudicated": len(adj),
        "r1_readers": r1_readers, "r2_readers": r2_readers,
    }


def _agreement(data, gradable, labels, ep_names):
    paired = [r for r in gradable if "r2" in r]
    if not paired:
        data["agreement"] = None
        return

    def line(name, a, b, dices):
        agree = sum(x == y for x, y in zip(a, b))
        ds = [d for d in dices if d is not None]
        return {"name": name, "n": len(a), "agree": agree, "agree_pct": _pct(agree / len(a)),
                "kappa": None if (k := M.cohen_kappa(a, b)) is None else round(k, 2),
                "dice": round(float(np.mean(ds)), 3) if ds else None, "n_dice": len(ds), "disagree": len(a) - agree,
                "pos_r1": sum(a), "pos_r2": sum(b)}

    rows = [line(f"{data['endpoint']['name']} (엔드포인트)", [r["r1_ep"] for r in paired],
                 [r["r2_ep"] for r in paired], [r["r1r2_ep_dice"] for r in paired])]
    for lab in labels:
        n = lab["name"]
        rows.append(line(n, [r["r1"][n] for r in paired], [r["r2"][n] for r in paired],
                         [r["r1r2_dice"][n] for r in paired]))
    dis = [r for r in paired if r["disagree"]]
    data["agreement"] = {
        "n": len(paired), "rows": rows, "n_disagree": len(dis),
        "disagreements": [{"name": r["name"], "r1": _lesions(r["r1"]), "r2": _lesions(r["r2"]),
                           "final": _lesions(r["final"]), "final_source": r["final_source"],
                           "dice": None if r["r1r2_ep_dice"] is None else round(r["r1r2_ep_dice"], 2),
                           "labels": r["disagree_labels"], "review": r["review"]} for r in dis],
    }


def _lesions(pres):
    found = [n for n, v in pres.items() if v]
    return ", ".join(found) if found else "병변 없음"


def _ai(data, gradable, labels, cfg):
    rows = [r for r in gradable if "ai" in r]
    if not rows:
        data["ai"] = None
        return
    cm = M.confusion([r["final_ep"] for r in rows], [r["ai_ep"] for r in rows])
    ss = M.sens_spec(cm)
    ep_d = [r["ai_ep_dice"] for r in rows if r["final_ep"] and r["ai_ep"] and r["ai_ep_dice"] is not None]
    per = []
    for lab in labels:
        n = lab["name"]
        c = M.confusion([r["final"][n] for r in rows], [r["ai"][n] for r in rows])
        s = M.sens_spec(c)
        ds = [r["ai_dice"][n] for r in rows if r["final"][n] and r["ai"][n] and r["ai_dice"][n] is not None]
        per.append({"name": n, **c, "n_pos": s["n_pos"], "sensitivity": _ci(s["sensitivity"]),
                    "specificity": _ci(s["specificity"]), "dice": round(float(np.mean(ds)), 3) if ds else None})
    data["ai"] = {"n": len(rows), "cm": cm, "n_pos": ss["n_pos"], "n_neg": ss["n_neg"],
                  "sensitivity": _ci(ss["sensitivity"]), "specificity": _ci(ss["specificity"]),
                  "dice": round(float(np.mean(ep_d)), 3) if ep_d else None, "per_label": per,
                  "baseline": cfg.get("baseline")}


def _subgroups(data, gradable, cfg):
    rows = [r for r in gradable if "ai" in r]
    if not rows:
        data["subgroups"] = []
        return
    overall = data["ai"]["sensitivity"]["value"] or 0
    groups = [{"name": "화질", "key": lambda r: QUALITY_KO[r["quality"]].split(" ")[0]}]
    for sg in cfg["subgroups"]:
        groups.append({"name": sg.get("name", sg["column"]), "key": lambda r, sg=sg: _subgroup_key(sg, r["meta"])})
    out = []
    for g in groups:
        buckets = {}
        for r in rows:
            k = g["key"](r)
            if k is not None:
                buckets.setdefault(k, []).append(r)
        for k in sorted(buckets, key=str):
            b = buckets[k]
            cm = M.confusion([r["final_ep"] for r in b], [r["ai_ep"] for r in b])
            ss = M.sens_spec(cm)
            sens = _ci(ss["sensitivity"])
            flag = sens["value"] is not None and overall - sens["value"] >= 100 * cfg["flag_drop"]
            out.append({"group": g["name"], "value": k, "n": len(b), "n_pos": ss["n_pos"], "tp": cm["tp"],
                        "sensitivity": sens, "specificity": _ci(ss["specificity"]), "flag": flag})
    data["subgroups"] = out


FN_CAUSES = {"edge": "영상 가장자리 병변 (스캔 범위 경계)", "quality": "경계 화질 (저대비·잡음)",
             "small": "소형 병변", "other": "기타"}
FP_CAUSES = {"overlap": "타 병변과 혼동", "quality": "경계 화질 artifact", "small": "소형 위양성 (잡음)",
             "other": "기타"}


def _errors(data, gradable, cfg):
    rows = [r for r in gradable if "ai" in r]
    if not rows:
        data["errors"] = None
        return
    fn, fp = [], []
    for r in rows:
        if r["final_ep"] and not r["ai_ep"]:
            cause = ("edge" if r["final_ep_edge"] else "quality" if r["quality"] == "borderline"
                     else "small" if r["final_ep_px"] < cfg["small_lesion_px"] else "other")
            fn.append({"name": r["name"], "cause": cause, "px": r["final_ep_px"], "quality": r["quality"],
                       "review": r["review"]})
        elif r["ai_ep"] and not r["final_ep"]:
            cause = ("overlap" if r.get("ai_fp_overlap") else "quality" if r["quality"] == "borderline"
                     else "small" if r["ai_ep_px"] < cfg["small_lesion_px"] else "other")
            fp.append({"name": r["name"], "cause": cause, "px": r["ai_ep_px"], "quality": r["quality"],
                       "overlap": r.get("ai_fp_overlap"), "review": r["review"]})

    def table(items, names, detail):
        out = []
        for k, label in names.items():
            its = [x for x in items if x["cause"] == k]
            if not its:
                continue
            sub = {}
            for x in its:
                if detail(x):
                    sub[detail(x)] = sub.get(detail(x), 0) + 1
            out.append({"cause": label, "key": k, "n": len(its), "detail": sorted(sub.items(), key=lambda kv: -kv[1]),
                        "examples": [x["name"] for x in its[:3]]})
        return out

    data["errors"] = {"fn": fn, "fp": fp,
                      "fn_table": table(fn, FN_CAUSES, lambda x: None),
                      "fp_table": table(fp, FP_CAUSES, lambda x: x.get("overlap"))}


def _readers(data, gradable, cfg):
    out = []
    for role, key in (("R1", "r1"), ("R2", "r2")):
        who_key = "annotator" if role == "R1" else "r2_annotator"
        for who in sorted({r.get(who_key) for r in gradable if key in r and r.get(who_key)}):
            mine = [r for r in gradable if key in r and r.get(who_key) == who]
            k = M.cohen_kappa([r[key + "_ep"] for r in mine], [r["final_ep"] for r in mine])
            info = cfg["readers"].get(who, {})
            out.append({"id": who, "role": info.get("role", role), "qualification": info.get("qualification", ""),
                        "sop": info.get("sop", ""), "gold_kappa": info.get("gold_kappa"), "n": len(mine),
                        "kappa_final": None if k is None else round(k, 2)})
    for who, info in cfg["readers"].items():
        if info.get("role") == "R3":
            out.append({"id": who, "role": "R3", "qualification": info.get("qualification", ""),
                        "sop": info.get("sop", "—"), "gold_kappa": info.get("gold_kappa", "기준 판독자"),
                        "n": data["procedure"]["n_adjudicated"], "kappa_final": None})
    data["readers"] = out


def _handling(data, rows, hashes, cfg):
    by_sha = {}
    for r in rows:
        if r["sha256"]:
            by_sha.setdefault(r["sha256"], []).append(r["name"])
    exact = [v for v in by_sha.values() if len(v) > 1]
    exact_pairs = {frozenset((a, b)) for g in exact for a in g for b in g if a != b}
    names = sorted(hashes)
    near = [p for p in M.near_duplicates(names, [hashes[n] for n in names], cfg["near_duplicate_corr"])
            if frozenset(p[:2]) not in exact_pairs]
    manifest = hashlib.sha256("\n".join(f"{r['name']}\t{r['sha256']}" for r in rows).encode()).hexdigest()
    data["handling"] = {"exact_duplicates": exact, "near_duplicates": near, "manifest_sha256": manifest,
                        "notes": cfg["data_handling"]}


def _recommend(data, cfg):
    rec = []
    sg = [s for s in data["subgroups"] if s["flag"] and s["group"] != "화질"]
    qsg = [s for s in data["subgroups"] if s["flag"] and s["group"] == "화질"]
    if sg or qsg:
        weak = sg + qsg
        p = min((s["sensitivity"]["value"] or 0) / 100 for s in weak)
        need = max(0, M.positives_needed(p) - min(s["n_pos"] for s in weak))
        names = ", ".join(f"{s['group']} {s['value']}" for s in weak)
        rec.append(("추가 데이터", f"민감도가 낮은 구간({names})의 양성 사례 약 {int(round(need, -1))}장을 추가 확보해 "
                                f"재학습 또는 보정 검토 (95% CI ±5%p 기준)",
                    f"저성능 구간 양성 사례 약 {int(round(need, -1))}장 추가 확보"))
    errs = data.get("errors") or {}
    fn_edge = sum(1 for x in errs.get("fn", []) if x["cause"] == "edge")
    if fn_edge:
        rec.append(("촬영 프로토콜", f"위음성 {fn_edge}건이 스캔 가장자리 병변 — 스캔 범위·중심 고정 가이드 보완",
                    "촬영 프로토콜 보완"))
    unread = next(c for c in data["qc"]["classes"] if c["key"] == "ungradable")
    if qsg or (unread["pct"] or 0) > 5:
        rec.append(("화질 필터", f"판독 불가 {unread['pct']}%, 경계 화질에서 성능 저하 — AI의 판독 불가 판정 임계값 재조정 검토",
                    "화질 필터 임계값 재조정"))
    fp_over = {}
    for x in errs.get("fp", []):
        if x["cause"] == "overlap":
            fp_over[x["overlap"]] = fp_over.get(x["overlap"], 0) + 1
    if fp_over:
        top = ", ".join(f"{k} {v}건" for k, v in sorted(fp_over.items(), key=lambda kv: -kv[1]))
        rec.append(("특이도", f"위양성 중 타 병변 혼동({top}) — 해당 병변 혼재 사례를 학습 데이터에 보강",
                    "혼동 병변 사례 보강"))
    agr = data.get("agreement")
    low = [r for r in (agr["rows"] if agr else []) if r["kappa"] is not None and r["kappa"] < 0.6 and r["pos_r1"] + r["pos_r2"] >= 5]
    if low:
        rec.append(("판독 기준", "판독자 간 일치도가 낮은 라벨(" + ", ".join(f"{r['name']} κ {r['kappa']}" for r in low)
                    + ") — SOP 정의 보완 및 판독자 재교육", "판독 SOP 보완"))
    if data["handling"]["exact_duplicates"] or data["handling"]["near_duplicates"]:
        rec.append(("데이터 정제", f"중복 영상 {len(data['handling']['exact_duplicates'])}쌍, 유사 중복 "
                                f"{len(data['handling']['near_duplicates'])}쌍 — 학습·검증 세트 간 누수 확인", "중복 영상 정리"))
    rec.append(("다음 단계", "위 보완 후 표준 검증(2인 전수 판독) 또는 reading center 본 검증 진행", ""))
    data["recommendations"] = [{"title": t, "text": x, "short": sh} for t, x, sh in rec]


def _limits(data, gradable):
    n = len(gradable)
    paired = data["procedure"]["n_paired"]
    lim = ["본 보고서는 <b>예비 검증(pre-validation)</b> 결과이며, 규제기관 제출용 임상 성능 평가를 대체하지 않습니다."]
    if n and paired < n:
        lim.append(f"제2 판독은 {_pct(paired / n)}% 표본({paired}/{n})에만 수행했으므로, 나머지 {_pct(1 - paired / n)}%의 "
                   f"라벨은 단일 판독 기반입니다.")
    if data["qc"]["source"]["auto"]:
        lim.append(f"화질 분류 중 {data['qc']['source']['auto']}건은 자동 화질 지표(신호 강도·포화·선명도)로 산출했으며, "
                   f"판독자 확인 전 참고값입니다.")
    lim.append("병변 유무는 해당 라벨 픽셀이 " + str(data["config"]["min_lesion_px"])
               + "px 이상일 때 '있음'으로 판정했습니다. Dice는 두 마스크 중 하나라도 병변이 있는 영상에서만 계산했습니다.")
    lim.append("서브그룹 표본 수가 작아 신뢰구간이 넓습니다. 서브그룹 결과는 경향 확인용이며 통계적 결론으로 해석하지 않습니다.")
    lim.append("결과는 의뢰사가 제공한 데이터의 분포에 한정되며, 다른 기관·인구집단으로 일반화할 수 없습니다.")
    org = data["config"]["report"]["org"] or "수행 기관"
    lim.append(f"{org}은(는) 의뢰 AI의 성능을 보증하지 않으며, 판독 절차와 결과 기록의 정확성에 대해 책임집니다.")
    data["limitations"] = lim


def _summary(data):
    qc = {c["key"]: c for c in data["qc"]["classes"]}
    n = data["qc"]["n"]
    g = n - qc["ungradable"]["n"]
    s = [f"{n:,}장 중 <b>{g:,}장({_pct(g / n) if n else 0}%)</b>이 판독 가능, {qc['ungradable']['n']}장"
         f"({qc['ungradable']['pct']}%)은 판독 불가로 분류됐습니다."]
    agr = data.get("agreement")
    if agr:
        e = agr["rows"][0]
        s.append(f"R1·R2 독립 판독(n={agr['n']})의 엔드포인트 일치율은 <b>{e['agree_pct']}%, κ {e['kappa']}</b>"
                 + (f", 평균 Dice {e['dice']}" if e["dice"] is not None else "") + f"였습니다. 라벨 단위 불일치 {agr['n_disagree']}건 중 "
                 f"{data['procedure']['n_adjudicated']}건은 R3가 조정했습니다.")
    ai = data.get("ai")
    if ai:
        s.append(f"AI의 {data['endpoint']['name']} 판별 성능은 <b>민감도 {ai['sensitivity']['value']}%, "
                 f"특이도 {ai['specificity']['value']}%</b>였습니다.")
        weak = [x for x in data["subgroups"] if x["flag"]]
        if weak:
            s.append("성능 저하는 주로 " + ", ".join(f"<b>{x['group']} {x['value']}</b>(민감도 {x['sensitivity']['value']}%)"
                                               for x in weak) + "에서 나타났습니다.")
        errs = data["errors"]
        if errs["fn"]:
            top = errs["fn_table"][0] if errs["fn_table"] else None
            if top:
                s.append(f"위음성 {len(errs['fn'])}건 중 {top['n']}건이 <b>{top['cause']}</b>와 관련됐습니다.")
    rec = [r for r in data["recommendations"] if r["title"] != "다음 단계"]
    if rec:
        s.append("본 검증 진행 전, " + " · ".join(f"<b>{r['short']}</b>" for r in rec[:3]) + "을(를) 권고합니다.")
    data["summary"] = s


def write_cases_csv(data, path):
    labels = [lab["name"] for lab in data["labels"]]
    cols = ["name", "annotator", "quality", "quality_source", "quality_reasons", "prompts", "final_source",
            "final_ep", "r1_ep", "r2_ep", "r1r2_ep_dice", "ai_ep", "ai_ep_dice", "disagree"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols + [f"final:{n}" for n in labels] + [f"ai:{n}" for n in labels])
        for r in data["cases"]:
            vals = [r.get(c) for c in cols]
            vals[cols.index("quality_reasons")] = "; ".join(r["quality_reasons"])
            w.writerow(vals + [int(r["final"][n]) for n in labels] + [int(r["ai"][n]) if "ai" in r else "" for n in labels])


def write_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
