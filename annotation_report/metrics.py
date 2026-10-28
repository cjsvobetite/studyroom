"""Statistics and image measures used by the report. Pure numpy / Pillow, no model needed."""
import math

import numpy as np
from PIL import Image, ImageFilter

Z95 = 1.959964


def wilson(k, n, z=Z95):
    """-> (proportion, low, high) with the Wilson score interval; (None, None, None) when n == 0."""
    if n <= 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def cohen_kappa(a, b):
    """Cohen's kappa for two equal-length sequences of category labels. None when undefined (n == 0)."""
    a, b = list(a), list(b)
    n = len(a)
    if n == 0:
        return None
    cats = sorted(set(a) | set(b), key=str)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    if pe >= 1:
        return 1.0 if po >= 1 else 0.0
    return (po - pe) / (1 - pe)


def dice(x, y):
    """Dice of two boolean masks; None when both are empty (no lesion to agree on)."""
    s = int(x.sum()) + int(y.sum())
    if s == 0:
        return None
    return 2.0 * int(np.logical_and(x, y).sum()) / s


def confusion(truth, pred):
    """truth / pred: sequences of bools -> dict tp, fp, fn, tn."""
    out = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for t, p in zip(truth, pred):
        out[("t" if t == p else "f") + ("p" if p else "n")] += 1
    return out


def sens_spec(cm):
    sens = wilson(cm["tp"], cm["tp"] + cm["fn"])
    spec = wilson(cm["tn"], cm["tn"] + cm["fp"])
    return {"sensitivity": sens, "specificity": spec, "n_pos": cm["tp"] + cm["fn"], "n_neg": cm["tn"] + cm["fp"]}


def positives_needed(p, half_width=0.05, z=Z95):
    """Positive cases needed for a +/- half_width 95% CI at sensitivity p (normal approximation)."""
    p = min(max(p, 0.5), 0.99)
    return math.ceil(z * z * p * (1 - p) / (half_width * half_width))


# ---------------------------------------------------------------------------------------------- image QC

QC_DEFAULTS = {
    # ungradable when any "bad_*" rule hits, borderline when any "low_*" rule hits, good otherwise
    "bad_signal": 35.0, "low_signal": 75.0,            # signal range: p90 - p20 grey level of a 4x-downsampled image
    "bad_saturated": 0.15,                             # fraction of pixels >= 250
    "bad_sharpness": 0.04, "low_sharpness": 0.08,      # mean gradient of the downsampled image / its std
}
QC_REASONS = {"signal": "신호 약함 (저대비)", "saturated": "노출 과다 (포화)", "sharpness": "초점 불량 (흐림)"}


def _small(gray, w=None, h=None):
    w = w or max(8, gray.shape[1] // 4)
    h = h or max(8, gray.shape[0] // 4)
    return Image.fromarray(gray).resize((w, h), Image.BOX)


def image_quality(gray, th=None):
    """-> (class 'good' | 'borderline' | 'ungradable', reasons [str], measures {name: float}).

    OCT signal strength is read as the grey-level range between the bright tissue and the background of a smoothed
    image; the defaults are tuned on the demo data and should be calibrated against reader QC on real scans."""
    th = {**QC_DEFAULTS, **(th or {})}
    small = np.asarray(_small(gray), np.float64)
    signal = float(np.percentile(small, 90) - np.percentile(small, 20))
    sat = float((gray >= 250).mean())
    grad = np.abs(np.diff(small, axis=0)).mean() + np.abs(np.diff(small, axis=1)).mean()
    sharp = float(grad / (small.std() + 1e-6))
    m = {"signal": round(signal, 2), "saturated": round(sat, 4), "sharpness": round(sharp, 4)}
    bad = [k for k, hit in (("signal", signal < th["bad_signal"]), ("saturated", sat > th["bad_saturated"]),
                            ("sharpness", sharp < th["bad_sharpness"])) if hit]
    if bad:
        return "ungradable", [QC_REASONS[k] for k in bad], m
    low = [k for k, hit in (("signal", signal < th["low_signal"]), ("sharpness", sharp < th["low_sharpness"])) if hit]
    if low:
        return "borderline", [QC_REASONS[k] for k in low], m
    return "good", [], m


def fingerprint(gray):
    """Unit vector of the image's fine structure (64x32 thumbnail minus its blur). Different B-scans share the coarse
    retinal shape, so the coarse image cannot tell them apart; a copy (re-saved, rescaled, brightened) keeps the
    fine structure and correlates > 0.99."""
    s = _small(gray, 64, 32)
    v = (np.asarray(s, np.float64) - np.asarray(s.filter(ImageFilter.GaussianBlur(2)), np.float64)).ravel()
    v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-9)


def near_duplicates(names, vecs, threshold=0.99, chunk=1024):
    """-> [[name_a, name_b, correlation]] for pairs at or above threshold."""
    if not names:
        return []
    V = np.asarray(vecs, np.float32)
    out = []
    for i in range(0, len(V), chunk):
        C = V[i:i + chunk] @ V.T
        for a, b in zip(*np.where(C >= threshold)):
            a += i
            if b > a:
                out.append([names[a], names[b], round(float(C[a - i, b]), 4)])
    return out


def edge_touch(mask, frac=0.05):
    """True when the mask's pixels reach the left / right band of the image (frac of the width)."""
    if not mask.any():
        return False
    cols = np.where(mask.any(axis=0))[0]
    w = mask.shape[1]
    return cols[0] < w * frac or cols[-1] >= w * (1 - frac)
