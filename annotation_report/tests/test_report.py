"""python -m pytest annotation_report/tests  (from the repository root)"""
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from annotation_report import analysis, metrics
from annotation_report.cli import run_report
from annotation_report.loader import Export


def test_wilson_and_kappa():
    p, lo, hi = metrics.wilson(187, 210)
    assert round(100 * p, 1) == 89.0 and round(100 * lo, 1) == 84.1 and round(100 * hi, 1) == 92.6
    assert metrics.wilson(0, 0) == (None, None, None)
    assert metrics.cohen_kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0
    assert metrics.cohen_kappa([1, 0, 1, 0], [0, 1, 0, 1]) == -1.0
    assert metrics.dice(np.zeros((2, 2), bool), np.zeros((2, 2), bool)) is None


def test_near_duplicates_ignore_similar_scans():
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (64, 128)).astype(np.uint8)
    other = rng.integers(0, 255, (64, 128)).astype(np.uint8)
    brighter = np.clip(base * 1.05 + 4, 0, 255).astype(np.uint8)
    names = ["a", "b", "c"]
    pairs = metrics.near_duplicates(names, [metrics.fingerprint(x) for x in (base, other, brighter)])
    assert [p[:2] for p in pairs] == [["a", "c"]]


def _zip(path, labels, cases, with_images=True):
    """cases: [(name, mask, image or None, extra record fields)]"""
    with zipfile.ZipFile(path, "w") as z:
        recs = []
        for name, mask, img, extra in cases:
            stem = name.rsplit(".", 1)[0]
            rec = {"name": name, "mask": f"masks/{stem}.png", "sha256": stem, "annotator": "r", **extra}
            buf = io.BytesIO()
            Image.fromarray(mask).save(buf, "PNG")
            z.writestr(rec["mask"], buf.getvalue())
            if with_images and img is not None:
                rec["image"] = f"imgs/{name}"
                buf = io.BytesIO()
                Image.fromarray(img).save(buf, "PNG")
                z.writestr(rec["image"], buf.getvalue())
            recs.append(rec)
        z.writestr("annotations.json", json.dumps({"format": "oct-platform-export/1", "project": "t", "modality": "oct",
                                                   "labels": labels, "cases": recs}))
    return str(path)


@pytest.fixture()
def exports(tmp_path):
    labels = [{"value": 1, "name": "IRF", "color": "#00B8D9"}, {"value": 2, "name": "SRF", "color": "#2455E6"}]
    rng = np.random.default_rng(1)
    img = lambda: np.clip(rng.normal(110, 50, (40, 60)), 0, 255).astype(np.uint8)  # noqa: E731
    pos = np.zeros((40, 60), np.uint8)
    pos[10:20, 20:35] = 2
    neg = np.zeros((40, 60), np.uint8)
    names = [f"s{i}.png" for i in range(6)]
    truth = [pos, pos, pos, neg, neg, neg]
    primary = _zip(tmp_path / "r1.zip", labels, [(n, m, img(), {}) for n, m in zip(names, truth)])
    # R2 swaps label order (SRF = 1): values must be remapped by name; disagrees on s2
    r2_labels = [{"value": 1, "name": "SRF", "color": "#000"}, {"value": 2, "name": "IRF", "color": "#000"}]
    r2 = [np.where(m == 2, 1, 0).astype(np.uint8) for m in truth[:3]]
    r2[2][:] = 0
    second = _zip(tmp_path / "r2.zip", r2_labels, [(n, m, None, {"annotator": "r2"}) for n, m in zip(names[:3], r2)])
    adjudicated = _zip(tmp_path / "r3.zip", labels, [(names[2], pos, None, {"review": {"text": "SRF 확인"}})])
    # AI: misses s0, false positive on s3, masks-only folder
    ai_dir = tmp_path / "ai" / "masks"
    ai_dir.mkdir(parents=True)
    for n, m in zip(names, [neg, pos, pos, pos, neg, neg]):
        Image.fromarray(m).save(ai_dir / n)
    return primary, second, adjudicated, str(tmp_path / "ai")


def test_build_report(exports, tmp_path):
    primary, second, adjudicated, ai = exports
    cfg = {"endpoint": {"name": "fluid", "labels": ["IRF", "SRF"]}, "min_lesion_px": 10,
           "quality": {"thresholds": {"bad_signal": -1, "low_signal": -1, "bad_sharpness": -1, "low_sharpness": -1}}}
    p = Export(primary)
    data = analysis.build(p, Export(second, p.labels), Export(adjudicated, p.labels), Export(ai, p.labels), {}, cfg)
    assert data["warnings"] == []
    agr = data["agreement"]
    assert agr["n"] == 3 and agr["rows"][0]["agree"] == 2 and agr["n_disagree"] == 1
    assert agr["rows"][2]["dice"] == 0.667                    # SRF remapped by name: s0, s1 = 1, s2 = 0
    assert agr["disagreements"][0]["review"] == "SRF 확인" and agr["disagreements"][0]["final_source"] == "R3"
    cm = data["ai"]["cm"]
    assert cm == {"tp": 2, "fp": 1, "fn": 1, "tn": 2}
    assert data["ai"]["sensitivity"]["value"] == 66.7
    assert [x["name"] for x in data["errors"]["fn"]] == ["s0.png"]
    assert data["recommendations"][-1]["title"] == "다음 단계"

    out = tmp_path / "out"
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    run_report(primary, second, adjudicated, ai, config=str(tmp_path / "cfg.json"), out=str(out))
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "어노테이션 예비 검증 보고서" in html and "부록 A" in html
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["qc"]["n"] == 6
    assert (out / "cases.csv").read_text(encoding="utf-8-sig").startswith("name,")


def test_primary_only(exports, tmp_path):
    primary = exports[0]
    data = run_report(primary, out=str(tmp_path / "o"))
    assert data["agreement"] is None and data["ai"] is None and data["subgroups"] == []
    assert "AI 예측 자료(--ai)가 없어" in (tmp_path / "o" / "report.html").read_text(encoding="utf-8")


def test_demo(tmp_path):
    from annotation_report.demo.make_demo import make_demo

    paths = make_demo(str(tmp_path), n=60, seed=3)
    data = run_report(**paths, out=str(tmp_path))
    assert data["qc"]["n"] == 60 and data["ai"]["n"] > 0
    assert data["handling"]["exact_duplicates"] == [["OCT-0018.png", "OCT-0058.png"]]
