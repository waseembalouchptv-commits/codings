"""Presenter face tracking -> protected zones.

The face box is the one region no caption, graphic or overlay may ever touch.
We sample the clean presenter video, detect faces (OpenCV Haar cascades: frontal +
profile, no model download needed), smooth the track and fill gaps. When detection
fails we fall back to a *large* central box, so failure makes layouts more
conservative, never less.
"""
import cv2
import numpy as np

from .media import FrameReader, probe

FALLBACK = [0.30, 0.10, 0.40, 0.60]  # x, y, w, h (normalised): protects the centre


def _detectors():
    base = cv2.data.haarcascades
    return (cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml"),
            cv2.CascadeClassifier(base + "haarcascade_profileface.xml"))


def _detect(gray, front, prof):
    h, w = gray.shape
    min_side = int(min(w, h) * 0.08)
    found = list(front.detectMultiScale(gray, 1.1, 6, minSize=(min_side, min_side)))
    if not found:
        found = list(prof.detectMultiScale(gray, 1.1, 5, minSize=(min_side, min_side)))
        if not found:
            flipped = list(prof.detectMultiScale(cv2.flip(gray, 1), 1.1, 5, minSize=(min_side, min_side)))
            found = [(w - x - fw, y, fw, fh) for x, y, fw, fh in flipped]
    if not found:
        return None
    x, y, fw, fh = max(found, key=lambda r: r[2] * r[3])  # presenter = largest face
    return [x / w, y / h, fw / w, fh / h]


def track(video, step=0.25, analysis_width=640):
    info = probe(video)
    aw = analysis_width
    ah = int(round(info["height"] * aw / info["width"] / 2) * 2)
    front, prof = _detectors()
    reader = FrameReader(video, aw, ah, 1.0 / step)
    raw = []
    for i, frame in enumerate(reader):
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY))
        raw.append(_detect(gray, front, prof))
    reader.close()
    found = [r for r in raw if r]
    coverage = len(found) / max(1, len(raw))
    boxes = _fill_and_smooth(raw)
    return {"step": step, "boxes": [[round(v, 4) for v in b] for b in boxes], "coverage": round(coverage, 3),
            "frame": [info["width"], info["height"]], "dominant": dominant_box(boxes)}


def _fill_and_smooth(raw, window=5):
    n = len(raw)
    if not any(raw):
        return [FALLBACK[:] for _ in range(n)]
    arr = np.array([r if r else [np.nan] * 4 for r in raw], dtype=float)
    idx = np.arange(n)
    for c in range(4):
        good = ~np.isnan(arr[:, c])
        arr[:, c] = np.interp(idx, idx[good], arr[good, c])
    # median filter kills single-frame false positives, then a short mean smooths jitter
    med = np.array([np.median(arr[max(0, i - window // 2): i + window // 2 + 1], axis=0) for i in range(n)])
    k = np.ones(3) / 3
    sm = np.stack([np.convolve(np.pad(med[:, c], 1, mode="edge"), k, mode="valid") for c in range(4)], axis=1)
    return sm.tolist()


def dominant_box(boxes):
    """Union of the 10th-90th percentile face extents: where the face *usually* is."""
    a = np.array(boxes)
    x0 = np.percentile(a[:, 0], 10)
    y0 = np.percentile(a[:, 1], 10)
    x1 = np.percentile(a[:, 0] + a[:, 2], 90)
    y1 = np.percentile(a[:, 1] + a[:, 3], 90)
    return [round(float(v), 4) for v in (x0, y0, x1 - x0, y1 - y0)]


def face_at(faces, t):
    boxes = faces["boxes"]
    i = min(len(boxes) - 1, max(0, int(round(t / faces["step"]))))
    return boxes[i]


def face_span(faces, t0, t1):
    """Union of the face box over a time span (what must stay clear for that whole span)."""
    boxes = faces["boxes"]
    i0 = max(0, int(t0 / faces["step"]))
    i1 = min(len(boxes), int(t1 / faces["step"]) + 2)
    sel = np.array(boxes[i0:i1] or [boxes[-1]])
    x0, y0 = sel[:, 0].min(), sel[:, 1].min()
    x1, y1 = (sel[:, 0] + sel[:, 2]).max(), (sel[:, 1] + sel[:, 3]).max()
    return [float(x0), float(y0), float(x1 - x0), float(y1 - y0)]


def protected(box, pad):
    """Grow a face box: hair, chin and expression need room too."""
    x, y, w, h = box
    return [x - w * pad, y - h * pad, w * (1 + 2 * pad), h * (1 + 2 * pad) + h * 0.6]  # extra below for mouth/neck
