"""Clean-take assembly, grading and the face-safe compositor.

Layouts (per beat in plan.json):
  presenter   full-frame presenter; framing wide/medium/close; optional push_in / pull_out
  split       presenter in their own half (face side), visual panel in the other half
  pip         visual full-frame, presenter in a small window in a lower corner
  full        visual full-frame, presenter removed (narration continues)

Every caption/graphic box is placed by searching for space that does not touch the
*protected face zone* (face box grown by STYLE.face_pad, unioned over the beat). If
no safe space exists the element is dropped and logged -- the face always wins.
"""
import math

import cv2
import numpy as np

from . import graphics as G
from .faces import face_span, protected
from .media import FrameReader, FrameWriter, probe, run, write_json
from .style import STYLE, font

ZOOM = {"wide": 1.0, "medium": 1.14, "close": 1.32}
SPLIT_PRESENTER = 0.44   # fraction of width the presenter keeps in a split


# ------------------------------------------------------------------ clean + grade

def estimate_grade(video, samples=12):
    info = probe(video)
    w = 320
    h = int(info["height"] * w / info["width"]) // 2 * 2
    step = max(1.0, info["duration"] / samples)
    reader = FrameReader(video, w, h, 1.0 / step)
    px = []
    for f in reader:
        px.append(f.reshape(-1, 3))
    reader.close()
    if not px:
        return {"r": 1, "g": 1, "b": 1, "gamma": 1.0}
    a = np.concatenate(px).astype(np.float32) / 255
    luma = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mid = a[(luma > 0.2) & (luma < 0.8)]
    if len(mid) < 100:
        mid = a
    mean = mid.mean(axis=0)
    gray = mean.mean()
    lim = STYLE.grade["max_wb_gain"]
    gains = [float(np.clip(1 + (gray / max(m, 1e-3) - 1) * 0.5, 1 - lim, 1 + lim)) for m in mean]
    med = float(np.median(luma))
    gamma = float(np.clip(1 + (0.45 - med) * 0.6, 0.9, 1.15))
    return {"r": round(gains[0], 3), "g": round(gains[1], 3), "b": round(gains[2], 3), "gamma": round(gamma, 3),
            "median_luma": round(med, 3)}


def grade_filter(g):
    s = STYLE.grade
    return (f"colorchannelmixer=rr={g['r']}:gg={g['g']}:bb={g['b']},"
            f"eq=contrast={s['contrast']}:saturation={s['saturation']}:gamma={g['gamma']}")


def make_clean(src, keep, out, grade, workdir, fps, voice_chain="anull"):
    """Restore the voice on the whole source, then concatenate keep ranges (8 ms fades so cuts
    never click) and grade. Denoising before cutting means every kept word gets identical treatment."""
    n = len(keep)
    parts = [f"[0:a]aresample=48000,{voice_chain},asplit={n}" + "".join(f"[src{i}]" for i in range(n)) + ";"]
    parts.append(f"[0:v]split={n}" + "".join(f"[vsrc{i}]" for i in range(n)) + ";")
    labels = []
    for i, (s, e) in enumerate(keep):
        d = e - s
        parts.append(f"[vsrc{i}]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];")
        parts.append(f"[src{i}]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.008,afade=t=out:st={max(0, d - 0.008):.3f}:d=0.008[a{i}];")
        labels.append(f"[v{i}][a{i}]")
    graph = "".join(parts) + "".join(labels) + f"concat=n={len(keep)}:v=1:a=1[cv][ca];" \
        f"[cv]{grade_filter(grade)},fps={fps}[gv]"
    script = workdir / "clean_graph.txt"
    script.write_text(graph)
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_complex_script", script, "-map", "[gv]", "-map", "[ca]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", out])
    return out


# ------------------------------------------------------------------ geometry

def _intersects(a, b):
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


class Camera:
    """A static virtual camera per beat: a crop window over the source chosen around the face."""

    def __init__(self, src_aspect, out_w, out_h, face, framing="medium", lead=0.0):
        self.As = src_aspect
        self.W, self.H = out_w, out_h
        Ao = out_w / out_h
        self.zmin = max(1.0, Ao / self.As)  # window must fit inside source
        fx, fy, fw, fh = face
        z = self.zmin * ZOOM.get(framing, 1.14)
        # keep the whole protected face inside the window
        while z > self.zmin and (fh * 1.9 * z > 1 or fw * 1.9 * z * self.As / Ao > 1):
            z = max(self.zmin, z * 0.95)
        self.z = z
        self.face = face
        self.lead = lead

    def window(self, zoom_mul=1.0):
        Ao = self.W / self.H
        z = max(self.zmin, self.z * zoom_mul)
        vh = 1 / z
        vw = vh * Ao / self.As
        fx, fy, fw, fh = self.face
        cx = fx + fw / 2 + self.lead * vw
        if z <= self.zmin * 1.001 and self.zmin == 1.0:
            cx = 0.5 * 0.6 + cx * 0.4   # wide: mostly centred, nudged toward the face
        eyes = fy + fh * 0.4
        x0 = float(np.clip(cx - vw / 2, 0, 1 - vw))
        y0 = float(np.clip(eyes - vh * 0.38, 0, 1 - vh))
        return x0, y0, vw, vh

    def matrix(self, src_w, src_h, region, zoom_mul=1.0):
        """Affine mapping source pixels -> output pixels for a region (x, y, w, h) of the output."""
        x0, y0, vw, vh = self.window(zoom_mul)
        rx, ry, rw, rh = region
        sx = rw / (vw * src_w)
        sy = rh / (vh * src_h)
        return np.array([[sx, 0, rx - x0 * src_w * sx], [0, sy, ry - y0 * src_h * sy]], np.float32)

    def to_out(self, box, region, zoom_mul=1.0):
        x0, y0, vw, vh = self.window(zoom_mul)
        rx, ry, rw, rh = region
        x, y, w, h = box
        return [rx + (x - x0) / vw * rw, ry + (y - y0) / vh * rh, w / vw * rw, h / vh * rh]


# ------------------------------------------------------------------ text

def _text_block(item, W, H, scale=1.0):
    """Pre-render a caption as an RGBA sprite with a soft shadow for legibility on real backgrounds."""
    style = item.get("style", "callout")
    text = item["text"]
    if style == "title":
        size, kind, weight, color, track = int(H * 0.085 * scale), "display", "Bold", STYLE.paper, 3
    elif style == "label":
        size, kind, weight, color, track = int(H * 0.045 * scale), "display", "SemiBold", STYLE.paper, 4
    else:
        size, kind, weight, color, track = int(H * 0.11 * scale), "display", "Bold", STYLE.paper, 2
    spr = G.text_sprite(text, kind, size, color, weight, track)
    pad = int(size * 0.35)
    h, w = spr.shape[:2]
    bar = max(4, int(size * 0.09))
    canvas = np.zeros((h + pad * 2 + bar * 3, w + pad * 2 + bar * 3, 4), np.uint8)
    # accent rule (label: left bar, others: underline)
    if style == "label":
        cv2.rectangle(canvas, (pad, pad + int(h * 0.15)), (pad + bar, pad + int(h * 0.92)), STYLE.accent + (255,), -1)
        ox, oy = pad + bar * 3, pad
    else:
        ox, oy = pad, pad
        cv2.rectangle(canvas, (ox + 2, oy + h + bar), (ox + 2 + int(w * 0.35), oy + h + bar * 2), STYLE.accent + (255,), -1)
    shadow = np.zeros_like(canvas)
    shadow[oy:oy + h, ox:ox + w, 3] = spr[..., 3]
    k = max(3, int(size * 0.25)) | 1
    shadow[..., 3] = (cv2.GaussianBlur(shadow[..., 3], (k, k), 0) * 0.65).astype(np.uint8)
    out = shadow.copy()
    G.blit_rgba(out, canvas)
    region = out[oy:oy + h, ox:ox + w]
    G.blit_rgba(region, spr)
    return out


def place_text(sprite_shape, W, H, avoid, prefer_side):
    """Find a position for a text sprite that touches none of the `avoid` boxes. None if impossible."""
    h, w = sprite_shape[:2]
    m = STYLE.margin * W
    xs = {"left": m, "right": W - m - w, "centre": (W - w) / 2}
    ys = [H * 0.62, H * 0.70, H * 0.45, H * 0.25, H * 0.12]
    order = [prefer_side, "centre", "right" if prefer_side == "left" else "left"]
    for side in order:
        for y in ys:
            box = [xs[side], y, w, h]
            if box[0] < 0 or box[1] + h > H - m * 0.5:
                continue
            if not any(_intersects(box, a) for a in avoid):
                return box
    return None


# ------------------------------------------------------------------ compositor

class Compositor:
    def __init__(self, clean_video, plan, faces, out_w=1920, out_h=1080, fps=None, draw_text=True):
        self.src = clean_video
        self.plan = plan
        self.faces = faces
        info = probe(clean_video)
        self.fps = fps or round(info["fps"])
        self.W, self.H = out_w, out_h
        self.sh = min(info["height"], int(out_h * 1.5))
        self.sw = int(round(info["width"] * self.sh / info["height"] / 2) * 2)
        self.As = info["width"] / info["height"]
        self.log = []
        self.draw_text = draw_text  # False: captions left out of the picture (exported as editable HTML)

    # -- per beat preparation
    def _prepare(self, b):
        W, H = self.W, self.H
        face = face_span(self.faces, b["start"], b["end"])
        prot = protected(face, STYLE.face_pad)
        lay = b["layout"]
        cam_cfg = b.get("camera", {})
        move = cam_cfg.get("move", "none")
        zoom_end = {"push_in": 1.07, "pull_out": 1 / 1.07}.get(move, 1.0)
        face_cx = face[0] + face[2] / 2
        face_side = "left" if face_cx < 0.5 else "right"
        rec = {"id": b.get("id"), "start": b["start"], "end": b["end"], "layout": lay, "elements": [],
               "dropped": []}
        prep = {"beat": b, "rec": rec, "zoom_end": zoom_end}

        if lay in ("presenter", "split", "pip"):
            if lay == "presenter":
                region = [0, 0, W, H]
                # rule of thirds: keep the presenter on their side, open space on the text side
                lead = 0.12 if face_side == "left" else -0.12
                cam = Camera(self.As, W, H, face, cam_cfg.get("framing", "medium"), lead=lead)
            elif lay == "split":
                side = b.get("visual", {}).get("presenter_side") or face_side
                pw = int(W * SPLIT_PRESENTER)
                region = [0, 0, pw, H] if side == "left" else [W - pw, 0, pw, H]
                cam = Camera(self.As, pw, H, face, "medium")
                prep["panel"] = [pw, 0, W - pw, H] if side == "left" else [0, 0, W - pw, H]
                prep["side"] = side
            else:
                pw = int(W * 0.24)
                ph = int(pw * 1.15)
                m = int(W * STYLE.margin * 0.6)
                corner = b.get("pip_corner") or ("left" if face_side == "left" else "right")
                region = [m, H - ph - m, pw, ph] if corner == "left" else [W - pw - m, H - ph - m, pw, ph]
                cam = Camera(self.As, pw, ph, face, "close")
                prep["panel"] = [0, 0, W, H]
            prep["cam"], prep["region"] = cam, region
            # protected zone in output coordinates, over the whole move
            pz = [cam.to_out(prot, region, 1.0), cam.to_out(prot, region, zoom_end)]
            x0 = min(p[0] for p in pz); y0 = min(p[1] for p in pz)
            x1 = max(p[0] + p[2] for p in pz); y1 = max(p[1] + p[3] for p in pz)
            fz = [max(region[0], x0), max(region[1], y0), min(region[0] + region[2], x1) - max(region[0], x0),
                  min(region[1] + region[3], y1) - max(region[1], y0)]
            rec["presenter_rect"] = [round(v, 1) for v in region]
            rec["face_zone"] = [round(v, 1) for v in fz]
            rec["face_box"] = [round(v, 1) for v in cam.to_out(face, region, 1.0)]
        if "panel" in prep:
            rec["elements"].append({"kind": "panel", "box": [round(v, 1) for v in prep["panel"]],
                                    "visual": b.get("visual", {}).get("type")})
        if lay != "presenter" and b.get("visual"):
            v = b["visual"]
            dur = b["end"] - b["start"]
            if v["type"] == "map":  # plan stores mention times on the edit timeline; maps animate in beat time
                v = dict(v, stops=[dict(st, t=max(0.0, st.get("t", b["start"]) - b["start"])) for st in v["stops"]])
            prep["visual"] = G.build(v, dur, self.fps) if v["type"] != "request" else None
        # captions
        prep["texts"] = []
        avoid = [rec["face_zone"]] if "face_zone" in rec else []
        if lay == "pip":
            avoid.append(rec["presenter_rect"])
        if lay == "split":
            avoid.append(prep["panel"])
        for item in b.get("text", []):
            if any(ord(c) >= 0x250 for c in item["text"]):
                rec["dropped"].append({"text": item["text"], "why": "on-screen text must be English"})
                continue
            words = len(item["text"].split())
            limit = 6 if item.get("style") == "title" else STYLE.max_caption_words
            if words > limit:
                rec["dropped"].append({"text": item["text"], "why": f"{words} words > {limit}: captions are editorial"})
                continue
            prefer = "right" if face_side == "left" else "left"
            box, spr = None, None
            for scale in (1.0, 0.8, 0.65):
                spr = _text_block(item, W, H, scale)
                box = place_text(spr.shape, W, H, avoid, prefer)
                if box:
                    break
            if not box:
                rec["dropped"].append({"text": item["text"], "why": "no space clear of the face"})
                continue
            prep["texts"].append((item, spr, box))
            rec["elements"].append({"kind": "text", "text": item["text"], "box": [round(v, 1) for v in box],
                                    "style": item.get("style", "callout"), "scale": scale,
                                    "start": item["start"], "end": item["end"]})
        self.log.append(rec)
        return prep

    def _presenter(self, frame, prep, k, canvas):
        cam, region = prep["cam"], prep["region"]
        zoom = math.exp(math.log(prep["zoom_end"]) * G.ease(k))
        M = cam.matrix(frame.shape[1], frame.shape[0], [0, 0, region[2], region[3]], zoom)
        sub = cv2.warpAffine(frame, M, (region[2], region[3]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        x, y, w, h = region
        if prep["beat"]["layout"] == "pip":
            mask = np.zeros((h, w), np.uint8)
            r = int(w * 0.08)
            cv2.rectangle(mask, (r, 0), (w - r, h), 255, -1)
            cv2.rectangle(mask, (0, r), (w, h - r), 255, -1)
            for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
                cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
            a = (mask.astype(np.float32) / 255)[..., None]
            dst = canvas[y:y + h, x:x + w].astype(np.float32)
            canvas[y:y + h, x:x + w] = (dst * (1 - a) + sub * a).astype(np.uint8)
        else:
            canvas[y:y + h, x:x + w] = sub

    def frame(self, frame, t, prep):
        b = prep["beat"]
        W, H = self.W, self.H
        dur = max(0.01, b["end"] - b["start"])
        tl = t - b["start"]
        k = tl / dur
        lay = b["layout"]
        canvas = np.empty((H, W, 3), np.uint8)
        if lay == "presenter":
            self._presenter(frame, prep, k, canvas)
        elif lay == "split":
            # background: blurred, darkened wide shot (continuity, never a hard black half)
            small = cv2.resize(frame, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
            bg = cv2.resize(cv2.GaussianBlur(small, (0, 0), 6), (W, H), interpolation=cv2.INTER_LINEAR)
            canvas[:] = (bg * 0.35).astype(np.uint8)
            self._presenter(frame, prep, k, canvas)
            px, py, pw, ph = prep["panel"]
            vis = prep["visual"].render(tl, pw, ph) if prep.get("visual") else G.backdrop(pw, ph)
            slide = 1 - G.ease_out(tl / STYLE.enter)
            off = int(slide * pw * (1 if prep["side"] == "left" else -1))
            x0 = px + off
            xs, xe = max(0, x0), min(W, x0 + pw)
            if xe > xs:
                canvas[py:py + ph, xs:xe] = vis[:, xs - x0:xe - x0]
                edge = xs if prep["side"] == "left" else xe - 3
                cv2.rectangle(canvas, (edge, 0), (edge + 2, H), STYLE.accent, -1)
        else:  # full / pip
            vis = prep["visual"].render(tl, W, H) if prep.get("visual") else G.backdrop(W, H)
            canvas[:] = vis
            if lay == "pip":
                self._presenter(frame, prep, k, canvas)
            if tl < 0.25 and lay == "full":   # short dissolve in from the presenter
                pres = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA) if frame.shape[1] != W else frame
                a = G.ease(tl / 0.25)
                canvas[:] = (canvas * a + pres * (1 - a)).astype(np.uint8)
        for item, spr, box in (prep["texts"] if self.draw_text else []):
            if item["start"] <= t < item["end"]:
                a_in = G.ease((t - item["start"]) / STYLE.enter)
                a_out = G.ease((item["end"] - t) / STYLE.exit)
                a = min(a_in, a_out)
                G.blit(canvas, spr, box[0], box[1] + (1 - a_in) * H * 0.015, a)
        return canvas

    def render(self, out_video, preview=False):
        beats = self.plan["beats"]
        total = self.plan["duration"]
        n = int(round(total * self.fps))
        reader = FrameReader(self.src, self.sw, self.sh, self.fps)
        writer = FrameWriter(out_video, self.W, self.H, self.fps, crf=22 if preview else 17,
                             preset="veryfast" if preview else "medium")
        bi, prep = -1, None
        last = None
        for i in range(n):
            t = i / self.fps
            frame = reader.read()
            if frame is None:
                frame = last if last is not None else np.zeros((self.sh, self.sw, 3), np.uint8)
            last = frame
            while bi + 1 < len(beats) and t >= beats[bi + 1]["start"]:
                bi += 1
                prep = self._prepare(beats[bi])
            writer.write(self.frame(frame, t, prep))
        reader.close()
        writer.close()
        return self.log


def mux(video, audio, out):
    run(["ffmpeg", "-y", "-v", "error", "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", out])
    return out


def save_log(log, path):
    write_json(path, log)
