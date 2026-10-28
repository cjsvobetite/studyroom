"""Reads the platform's export layout (Project -> Export), from a .zip or an unpacked folder.

  imgs/<image>          source image
  masks/<stem>.png      8-bit, pixel value = label value, 0 = background
  annotations.json      {format, project, modality, exported_at, labels [{value, name, color}], text_fields,
                         cases [{name, image, mask, sha256, width, height, status, annotator, texts, review,
                                 comments, label_pixels, submitted_at, ...}]}

An AI prediction set may be the same layout, or just a folder of masks/<stem>.png (labels then follow the primary
export). Every mask is remapped to the primary export's label values by label name, like the platform's import.
"""
import io
import json
import os
import zipfile
from dataclasses import dataclass, field

import numpy as np
from PIL import Image


@dataclass
class Case:
    name: str
    stem: str
    image: str | None
    mask: str | None
    sha256: str = ""
    width: int = 0
    height: int = 0
    status: str = ""
    annotator: str | None = None
    texts: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    comments: list = field(default_factory=list)
    submitted_at: str | None = None
    points: str | None = None
    lines: str | None = None


class Export:
    def __init__(self, path, ref_labels=None):
        self.path = path
        self._zip = zipfile.ZipFile(path) if os.path.isfile(path) else None
        names = self._names()
        meta = json.loads(self.read("annotations.json")) if "annotations.json" in names else None
        if meta is None and ref_labels is None:
            raise ValueError(f"{path}: annotations.json is missing (only AI prediction sets may omit it)")
        self.meta = meta or {}
        self.project = self.meta.get("project", os.path.basename(path))
        self.modality = self.meta.get("modality", "")
        self.exported_at = self.meta.get("exported_at")
        self.format = self.meta.get("format", "masks-only")
        self.labels = [{"value": int(lab["value"]), "name": str(lab["name"]), "color": lab.get("color", "#888888")}
                       for lab in self.meta.get("labels", ref_labels or [])]
        self.text_fields = self.meta.get("text_fields", [])
        self.lut = np.arange(256, dtype=np.uint8)
        if ref_labels is not None:                   # remap mask values to the reference export's label values
            by_name = {lab["name"].lower(): lab["value"] for lab in ref_labels}
            self.lut = np.zeros(256, np.uint8)
            for lab in self.labels:
                if 0 < lab["value"] < 256:
                    self.lut[lab["value"]] = by_name.get(lab["name"].lower(), 0)
        self.cases = {}
        if meta is not None:
            for rec in meta.get("cases", []):
                name = os.path.basename(str(rec.get("name") or rec.get("image") or ""))
                if not name:
                    continue
                self.cases[name] = Case(
                    name=name, stem=os.path.splitext(os.path.basename(rec.get("mask") or name))[0],
                    image=rec.get("image"), mask=rec.get("mask"), sha256=rec.get("sha256", ""),
                    width=int(rec.get("width") or 0), height=int(rec.get("height") or 0),
                    status=rec.get("status", ""), annotator=rec.get("annotator"), texts=rec.get("texts") or {},
                    review=rec.get("review") or {}, comments=rec.get("comments") or [],
                    submitted_at=rec.get("submitted_at"), points=rec.get("points"), lines=rec.get("lines"))
        else:                                        # masks-only prediction set: keyed by mask stem
            for n in names:
                if n.startswith("masks/") and n.lower().endswith(".png"):
                    stem = os.path.splitext(os.path.basename(n))[0]
                    self.cases[stem] = Case(name=stem, stem=stem, image=None, mask=n)

    def _names(self):
        if self._zip is not None:
            return set(self._zip.namelist())
        out = set()
        for d, _, files in os.walk(self.path):
            for f in files:
                out.add(os.path.relpath(os.path.join(d, f), self.path).replace(os.sep, "/"))
        return out

    def read(self, rel):
        if self._zip is not None:
            return self._zip.read(rel)
        with open(os.path.join(self.path, rel), "rb") as f:
            return f.read()

    def find(self, case):
        """This export's case for `case` (another export's), matched by image name, then stem, then sha256."""
        c = self.cases.get(case.name) or self.cases.get(case.stem)
        if c is None and case.sha256:
            c = next((x for x in self.cases.values() if x.sha256 and x.sha256 == case.sha256), None)
        return c

    def prompt_count(self, case):
        """Model-assist prompts (points + lines) stored with the annotation; 0 when the export has none."""
        n = 0
        for rel in (case.points, case.lines):
            try:
                n += len(json.loads(self.read(rel))) if rel else 0
            except (KeyError, FileNotFoundError, ValueError):
                pass
        return n

    def mask(self, case, shape=None):
        """Class-index mask in reference label values, or None. Missing mask with a known shape -> all background."""
        if case is None or not case.mask:
            return np.zeros(shape, np.uint8) if shape else None
        m = np.array(Image.open(io.BytesIO(self.read(case.mask))))
        if m.ndim == 3:
            m = m[..., 0]
        return self.lut[m.astype(np.uint8)]

    def image(self, case):
        """Grey image (uint8) or None when the export has no image for it."""
        if case is None or not case.image:
            return None
        try:
            img = Image.open(io.BytesIO(self.read(case.image)))
        except (KeyError, FileNotFoundError, OSError):
            return None
        a = np.array(img)
        if a.dtype != np.uint8:                      # 16-bit TIFF and the like
            a = (a.astype(np.float64) / max(float(a.max()), 1.0) * 255).astype(np.uint8)
        if a.ndim == 3:
            a = a[..., :3].mean(axis=2).astype(np.uint8)
        return a
