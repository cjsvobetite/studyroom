"""Synthetic OCT B-scans and exports in the platform layout, for the example report. All data is fictional.

Scenario (mirrors the Notion example): a client's fluid-detection AI, originally trained on device A, is checked on
images from a new device B. R1 reads every image, R2 independently reads a 20% sample, R3 adjudicates the
disagreements, and the AI's automatic segmentation is compared with the confirmed labels.
"""
import io
import json
import os
import zipfile

import numpy as np
from PIL import Image, ImageFilter

from ..metrics import dice, edge_touch

W, H = 320, 160
LABELS = [  # the platform's default OCT labels (modalities.py), value = index + 1
    ("IRF", "#00B8D9"), ("SRF", "#2455E6"), ("Drusen and drusenoid PED", "#FFB300"), ("Other PED", "#FF7043"),
    ("Hyperreflective foci", "#D81B60"), ("SHRM", "#8E24AA"), ("Fibrotic HRM / subretinal fibrosis", "#5D4037"),
    ("Microaneurysm-like lesion", "#43A047")]
IRF, SRF, DRUSEN, HRF = 1, 2, 3, 5
YY, XX = np.mgrid[0:H, 0:W]


def _ellipse(cx, cy, rx, ry):
    return ((XX - cx) / rx) ** 2 + ((YY - cy) / ry) ** 2 <= 1


def _grow(m, r=1):
    out = m.copy()
    for _ in range(abs(r)):
        nb = [np.roll(out, s, a) for s in (1, -1) for a in (0, 1)]
        out = np.logical_or.reduce([out, *nb]) if r > 0 else np.logical_and.reduce([out, *nb])
    return out


def _scan(rng, quality, case):
    """-> (grey image, truth mask, facts) for one synthetic B-scan."""
    x = np.arange(W)
    ilm = 38 + 6 * np.sin(x / W * np.pi * rng.uniform(0.6, 1.2)) - 14 * np.exp(-((x - W / 2) / 22) ** 2)
    thick = 62 + rng.normal(0, 3)
    rpe = ilm + thick + 14 * np.exp(-((x - W / 2) / 22) ** 2) * 0.9
    img = np.full((H, W), 18.0)
    retina = (YY >= ilm[None, :]) & (YY < rpe[None, :])
    depth = (YY - ilm[None, :]) / thick
    img[retina] = (95 + 35 * np.sin(depth[retina] * 9)) * 0.9
    choroid = (YY >= rpe[None, :] + 3) & (YY < rpe[None, :] + 40)
    img[choroid] = 80 - (YY - rpe[None, :])[choroid] * 1.2
    mask = np.zeros((H, W), np.uint8)
    facts = {"edge": False}
    rpe_y = rpe.copy()
    if case["drusen"]:                                 # RPE bumps
        for _ in range(rng.integers(1, 3)):
            cx, w, hgt = rng.uniform(40, W - 40), rng.uniform(10, 20), rng.uniform(5, 10)
            bump = hgt * np.exp(-((x - cx) / w) ** 2)
            region = (YY >= (rpe - bump)[None, :]) & (YY < rpe[None, :]) & (bump[None, :] > 1)
            mask[region] = DRUSEN
            img[region] = 140
            rpe_y = np.minimum(rpe_y, rpe - bump)
    band = (YY >= rpe_y[None, :]) & (YY < rpe_y[None, :] + 4)
    img[band] = 215
    if case["srf"]:
        cx = rng.uniform(W * 0.3, W * 0.7) if not case["edge"] else rng.choice([rng.uniform(8, 24), rng.uniform(W - 24, W - 8)])
        rx, ry = rng.uniform(14, 40) * case["size"], rng.uniform(5, 11) * case["size"]
        cy = np.interp(cx, x, rpe_y) - ry * 0.9
        m = _ellipse(cx, cy, rx, ry) & (YY < rpe_y[None, :]) & (YY > ilm[None, :] + 8)
        mask[m] = SRF
        img[m] = 26
        facts["edge"] = facts["edge"] or case["edge"]
    if case["irf"]:
        for _ in range(rng.integers(1, 4)):
            cx = rng.uniform(W * 0.25, W * 0.75) if not case["edge"] else rng.uniform(6, 22)
            r = rng.uniform(4, 10) * case["size"]
            cy = np.interp(cx, x, ilm) + rng.uniform(0.35, 0.6) * thick
            m = _ellipse(cx, cy, r, r * rng.uniform(0.6, 1.0)) & retina
            mask[m] = IRF
            img[m] = 24
    if case["hrf"]:
        for _ in range(rng.integers(2, 6)):
            cx, cy = rng.uniform(30, W - 30), rng.uniform(55, 95)
            m = _ellipse(cx, cy, 2.2, 2.2) & retina
            mask[m] = HRF
            img[m] = 235
    gain, noise, blur = {"good": (1.0, 0.25, 0.4), "borderline": (0.62, 0.42, 1.0), "ungradable": (0.22, 0.6, 2.6)}[quality]
    img = 12 + (img - 12) * gain
    img = img * rng.gamma(1 / noise ** 2, noise ** 2, img.shape)
    pil = Image.fromarray(img.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(pil), mask, facts


def _perturb(rng, mask, miss_small=0.0, grow=0):
    """Reader / AI variation: boundary shifted by `grow` px; small components dropped with prob miss_small."""
    out = mask.copy()
    for v in np.unique(mask):
        if v == 0:
            continue
        m = mask == v
        g = _grow(m, grow) if grow else m
        if miss_small and m.sum() < 220 and rng.random() < miss_small:
            g = np.zeros_like(m)
        out[(out == v) & ~g] = 0
        out[g & (out == 0)] = v
    return out


def _export(path, project, cases, labels=LABELS):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        recs = []
        for c in cases:
            stem = os.path.splitext(c["name"])[0]
            rec = {"case_id": c["id"], "name": c["name"], "image": None, "mask": f"masks/{stem}.png",
                   "points": f"points/{stem}.json", "lines": f"lines/{stem}.json", "width": W, "height": H,
                   "sha256": c["sha256"], "status": "submitted", "version": 1, "source": "platform",
                   "texts": c.get("texts", {}), "review": c.get("review", {"text": "", "by": None, "at": None}),
                   "comments": [], "label_pixels": {}, "annotator": c["annotator"],
                   "submitted_at": "2026-10-20T09:00:00Z", "updated_at": "2026-10-20T09:00:00Z"}
            if c.get("png") is not None:
                rec["image"] = f"imgs/{c['name']}"
                z.writestr(rec["image"], c["png"])
            buf = io.BytesIO()
            Image.fromarray(c["mask"]).save(buf, "PNG")
            z.writestr(rec["mask"], buf.getvalue())
            z.writestr(rec["points"], json.dumps(c.get("points", [])))
            z.writestr(rec["lines"], json.dumps([]))
            recs.append(rec)
        z.writestr("annotations.json", json.dumps({
            "format": "oct-platform-export/1", "project": project, "modality": "oct",
            "exported_at": "2026-10-27T10:00:00Z", "statuses": ["submitted"],
            "mask_encoding": "8-bit PNG, pixel value = label value, 0 = background",
            "labels": [{"value": i + 1, "name": n, "color": col} for i, (n, col) in enumerate(labels)],
            "text_fields": [{"key": "clinical_description", "label": "Clinical Description", "type": "textarea"}],
            "cases": recs}, ensure_ascii=False, indent=1))


def make_demo(out, n=400, seed=7, filled=False):
    """Writes R1 / R2 / R3 / AI exports, meta.csv and report_config.json into `out`; -> kwargs for run_report.

    filled=True also writes fictional specialist notes (R3 adjudication reasons, R1 review notes on the AI's error
    cases) and uses report_config_filled.json, whose `expert` / `llm` entries fill every red and blue slot."""
    import hashlib

    rng = np.random.default_rng(seed)
    os.makedirs(out, exist_ok=True)
    r1, r2, r3, ai, meta = [], [], [], [], ["name,age,sex,device,eye"]
    for i in range(n):
        age = int(rng.integers(45, 90))
        old = age >= 65
        quality = rng.choice(["good", "borderline", "ungradable"],
                             p=[0.62, 0.32, 0.06] if old else [0.80, 0.16, 0.04])
        fluid = rng.random() < 0.3
        case = {"irf": fluid and rng.random() < 0.6, "srf": fluid and rng.random() < 0.65,
                "drusen": rng.random() < (0.45 if old else 0.2), "hrf": rng.random() < 0.15,
                "edge": fluid and rng.random() < 0.08, "size": rng.choice([0.45, 1.0, 1.2], p=[0.18, 0.62, 0.2])}
        if fluid and not (case["irf"] or case["srf"]):
            case["srf"] = True
        img, truth, facts = _scan(rng, quality, case)
        name = f"OCT-{i + 1:04d}.png"
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, "PNG")
        png = buf.getvalue()
        sha = hashlib.sha256(png).hexdigest()
        if i in (57, 211):                           # re-uploaded scans for appendix C: an exact and a near duplicate
            src = r1[i - 40]
            truth = src["truth"]
            png, sha = src["png"], src["sha256"]
            if i == 211:                             # re-exported brighter: different bytes, same scan
                a = np.asarray(Image.open(io.BytesIO(png)), np.float64) * 1.08 + 3
                buf = io.BytesIO()
                Image.fromarray(a.clip(0, 255).astype(np.uint8)).save(buf, "PNG")
                png = buf.getvalue()
                sha = hashlib.sha256(png).hexdigest()
        r1_reader = "reader_kim" if i % 2 == 0 else "reader_lee"
        m1 = _perturb(rng, truth, miss_small=0.05, grow=int(rng.integers(-1, 2)))
        desc = ("SRF 및 IRF 관찰" if case["srf"] and case["irf"] else "SRF 관찰" if case["srf"]
                else "IRF 관찰" if case["irf"] else "삼출성 액체 없음") + (", 드루젠 동반" if case["drusen"] else "")
        prompts = [{"x": int(rng.integers(0, W)), "y": int(rng.integers(40, 120)), "label": "SRF", "value": 2,
                    "positive": True}] if fluid and rng.random() < 0.7 else []
        row = {"id": i + 1, "name": name, "png": png, "sha256": sha, "mask": m1, "truth": truth,
               "annotator": r1_reader, "texts": {"clinical_description": desc}, "points": prompts}
        r1.append(row)
        sex = rng.choice(["F", "M"])
        meta.append(f"{name},{age},{sex},장비 B,{rng.choice(['OD', 'OS'])}")
        if quality != "ungradable" and rng.random() < 0.2:
            m2 = _perturb(rng, truth, miss_small=0.25, grow=int(rng.integers(-2, 3)))
            if rng.random() < 0.06:                  # occasional real disagreement on SRF vs drusen
                m2[truth == DRUSEN] = SRF
            r2.append({"id": i + 1, "name": name, "sha256": sha, "mask": m2, "annotator": "reader_park"})
            d = dice(np.isin(m1, [IRF, SRF]), np.isin(m2, [IRF, SRF]))
            pres = lambda m: {v for v in (IRF, SRF, DRUSEN, HRF) if (m == v).sum() >= 30}  # noqa: E731
            if pres(m1) != pres(m2) or (d is not None and d < 0.5):
                rec = {"id": i + 1, "name": name, "sha256": sha, "mask": truth, "annotator": "reader_r3"}
                if filled:
                    diff = pres(m1) ^ pres(m2)
                    note = ("소형 IRF 확인 (R2 누락)" if IRF in diff else "RPE 융기는 드루젠, SRF 아님" if SRF in diff
                            else "HRF 2개 이상 확인" if HRF in diff else "RPE 융기 확인, 드루젠 포함" if DRUSEN in diff
                            else "액체 경계 재설정")
                    rec["review"] = {"text": note, "by": "reader_r3", "at": "2026-10-24T09:00:00Z"}
                r3.append(rec)
        # AI: misses on borderline quality / older eyes / edge, confuses drusen with SRF, some noise blobs
        p_miss = 0.04 + (0.16 if quality == "borderline" else 0) + (0.05 if old else 0) + (0.6 if facts["edge"] else 0)
        pa = _perturb(rng, truth, miss_small=0.5, grow=int(rng.integers(-2, 2)))
        if rng.random() < p_miss:
            pa[np.isin(pa, [IRF, SRF])] = 0
        if case["drusen"] and rng.random() < 0.10:
            pa[truth == DRUSEN] = SRF
        if rng.random() < 0.04:
            pa[_ellipse(rng.uniform(60, W - 60), rng.uniform(60, 90), 6, 4) & (pa == 0)] = IRF
        ai.append({"id": i + 1, "name": name, "sha256": sha, "mask": pa, "annotator": "AI"})
        fluid_px = lambda m: int(np.isin(m, [IRF, SRF]).sum())  # noqa: E731
        if filled and (fluid_px(truth) >= 30) != (fluid_px(pa) >= 30):
            fluid_t, fluid_p = np.isin(truth, [IRF, SRF]), np.isin(pa, [IRF, SRF])
            if fluid_px(truth) >= 30:                # missed: same order as analysis.FN_CAUSES
                note = ("스캔 가장자리 소량 액체, 중심와 영향 없음" if edge_touch(fluid_t) else
                        "신호 약한 영상에서 저반사 공간 대비 낮음" if quality == "borderline" else
                        "소형 저반사 병변, 임상적 의미 낮음" if fluid_t.sum() < 400 else "드루젠 인접 SRF, 경계 판단 어려움")
            else:                                    # false alarm: same order as analysis.FP_CAUSES
                note = ("RPE 위 드루젠 융기, 액체 아님" if (fluid_p & (truth > 0)).sum() > 0.3 * fluid_p.sum() else
                        "잡음이 많은 영상의 저반사 반점" if quality == "borderline" else
                        "잡음성 소형 예측" if fluid_p.sum() < 400 else "판단 보류")
            r1[-1]["review"] = {"text": note, "by": "reader_r3", "at": "2026-10-25T09:00:00Z"}

    paths = {k: os.path.join(out, f"{k}.zip") for k in ("primary", "second", "adjudicated", "ai")}
    _export(paths["primary"], "OCT 장비 B 예비검증 (R1)", r1)
    _export(paths["second"], "OCT 장비 B 예비검증 (R2)", r2)
    _export(paths["adjudicated"], "OCT 장비 B 예비검증 (R3 조정)", r3)
    _export(paths["ai"], "의뢰 AI 예측", ai)
    with open(os.path.join(out, "meta.csv"), "w", encoding="utf-8") as f:
        f.write("\n".join(meta) + "\n")
    cfg_src = os.path.join(os.path.dirname(__file__), "report_config_filled.json" if filled else "report_config.json")
    cfg_dst = os.path.join(out, "report_config.json")
    with open(cfg_src, encoding="utf-8") as f, open(cfg_dst, "w", encoding="utf-8") as g:
        g.write(f.read())
    return {**paths, "meta": os.path.join(out, "meta.csv"), "config": cfg_dst}
