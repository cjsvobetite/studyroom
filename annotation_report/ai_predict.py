"""AI predictions for an export through the platform's model service (model_server/, POST /v1/predict).

A predict call with no prompts returns the model's automatic segmentation of every class. Model classes are mapped to
the export's labels by name (the platform's `model_class` is the label name by default). The output is a folder in
the export layout (annotations.json + masks/<stem>.png) that `report --ai` reads.
"""
import io
import json
import os
import urllib.error
import urllib.request
import uuid

import numpy as np
from PIL import Image

from .loader import Export


def _post(url, fields, files, timeout=300):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    for k, (fn, data) in files.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return {k.lower(): v for k, v in r.headers.items()}, r.read()


def predict_export(export_path, model_url, out):
    exp = Export(export_path)
    model_url = model_url.rstrip("/")
    with urllib.request.urlopen(model_url + "/v1/meta", timeout=10) as r:
        meta = json.loads(r.read())
    classes = [c.lower() for c in meta.get("classes", [])]
    lut = np.zeros(256, np.uint8)                    # model value (class index + 1) -> export label value
    for lab in exp.labels:
        if lab["name"].lower() in classes:
            lut[classes.index(lab["name"].lower()) + 1] = lab["value"]
    os.makedirs(os.path.join(out, "masks"), exist_ok=True)
    records = []
    for case in exp.cases.values():
        gray = exp.image(case)
        if gray is None:
            continue
        buf = io.BytesIO()
        Image.fromarray(gray).save(buf, "PNG")
        try:
            headers, body = _post(model_url + "/v1/predict", {"key": f"report:{case.sha256[:16] or case.stem}",
                                                             "prompts": "{}"}, {"image": ("image.png", buf.getvalue())})
        except urllib.error.HTTPError as e:
            print(f"{case.name}: model error {e.code}")
            continue
        h, w = map(int, headers["x-shape"].split(","))
        pred = lut[np.frombuffer(body, np.uint8).reshape(h, w)]
        rel = f"masks/{case.stem}.png"
        Image.fromarray(pred).save(os.path.join(out, rel))
        records.append({"name": case.name, "mask": rel, "sha256": case.sha256, "width": w, "height": h,
                        "annotator": f"AI ({meta.get('ckpt') or meta.get('backbone') or 'model'})"})
    with open(os.path.join(out, "annotations.json"), "w", encoding="utf-8") as f:
        json.dump({"format": "oct-platform-export/1", "project": f"{exp.project} — AI", "modality": exp.modality,
                   "labels": exp.labels, "model": meta, "cases": records}, f, ensure_ascii=False, indent=1)
    return len(records)
