"""Motion graphics, drawn procedurally so every frame is original and licence-clean.

Each visual is an object with  render(t_local, w, h) -> RGB uint8 frame, where
t_local is seconds since the visual appeared and (w, h) the panel it fills. The
compositor decides *where* the panel goes (full frame or the half away from the
presenter's face).
"""
import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import gazetteer
from .style import STYLE, font


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def lerp(a, b, k):
    return a + (b - a) * k


@lru_cache(maxsize=8)
def backdrop(w, h, seed=0):
    """Ink background with soft vignette and fine grain (avoids flat digital look)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w * 0.5) / w) ** 2 + ((yy - h * 0.45) / h) ** 2)
    v = np.clip(1.15 - d * 0.9, 0.55, 1.0)[..., None]
    base = np.array(STYLE.ink_2, np.float32) * v + np.array(STYLE.ink, np.float32) * (1 - v)
    rng = np.random.default_rng(seed)
    base += rng.normal(0, 2.2, (h, w, 1)).astype(np.float32)
    return np.clip(base, 0, 255).astype(np.uint8)


def text_sprite(text, kind, size, color, weight="Bold", tracking=0):
    """Render text to an RGBA numpy sprite."""
    f = font(kind, size, weight)
    if tracking:
        widths = [f.getlength(c) + tracking for c in text]
        w = int(sum(widths)) + 4
    else:
        w = int(f.getlength(text)) + 4
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (max(1, w), asc + desc + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if tracking:
        x = 2
        for c, cw in zip(text, widths):
            d.text((x, 2), c, font=f, fill=color + (255,))
            x += cw
    else:
        d.text((2, 2), text, font=f, fill=color + (255,))
    return np.array(img)


def blit(dst, sprite, x, y, alpha=1.0):
    """Alpha-blend an RGBA sprite onto an RGB frame in place (clipped)."""
    h, w = sprite.shape[:2]
    H, W = dst.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1 or alpha <= 0:
        return
    s = sprite[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    a = s[..., 3:4] / 255.0 * alpha
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (region * (1 - a) + s[..., :3] * a).astype(np.uint8)


def blit_rgba(dst, src):
    """Composite an RGBA sprite over an RGBA canvas of the same size, in place."""
    sa = src[..., 3:4].astype(np.float32) / 255
    da = dst[..., 3:4].astype(np.float32) / 255
    oa = sa + da * (1 - sa)
    rgb = (src[..., :3] * sa + dst[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-6)
    dst[..., :3] = rgb.astype(np.uint8)
    dst[..., 3:4] = (oa * 255).astype(np.uint8)


def wrap(text, kind, size, max_w, weight="Regular"):
    f = font(kind, size, weight)
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if f.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- maps

class MapJourney:
    """Animated documentary map: establish -> fly to each stop -> pin + label.

    stops: [{name, lon, lat, kind, t (seconds since visual start), bbox?}]
    """

    def __init__(self, stops, duration, title=None):
        self.g = gazetteer.geo()
        self.stops = sorted(stops, key=lambda s: s.get("t", 0))
        self.duration = duration
        self.title = title
        self._sprites = {}
        self.keys = self._keyframes()

    @staticmethod
    def _span_for(stop):
        if stop.get("bbox"):
            x0, y0, x1, y1 = stop["bbox"]
            return max(x1 - x0, (y1 - y0) * 1.6) * 1.35
        return {"country": 22, "province": 11, "desert": 10, "range/mtn": 9, "river": 14,
                "city": 6.5, "town": 5.5, "site": 5, "pass": 5}.get(stop.get("kind"), 7)

    def _keyframes(self):
        first = self.stops[0] if self.stops else {"lon": 69.3, "lat": 30.0}
        # establishing view: Pakistan and neighbours, gently offset toward the first stop
        keys = [(0.0, lerp(69.3, first["lon"], 0.3), lerp(29.8, first["lat"], 0.3), 24.0)]
        for s in self.stops:
            t = max(keys[-1][0] + 0.6, s.get("t", 0.0))
            keys.append((t, s["lon"], s["lat"], self._span_for(s)))
        return keys

    def camera(self, t):
        keys = self.keys
        fly = 1.3
        cur = keys[0]
        for k in keys[1:]:
            if t < k[0]:
                break
            a = ease((t - k[0]) / fly)
            cur = (k[0], lerp(cur[1], k[1], a), lerp(cur[2], k[2], a),
                   math.exp(lerp(math.log(cur[3]), math.log(k[3]), a)))
        # slow drift keeps the map alive between moves
        return cur[1] + 0.02 * t, cur[2], cur[3] * (1 - 0.006 * t)

    def _project(self, pts, cam, w, h):
        """Local equirectangular projection: longitude compressed by cos(lat) so shapes keep proportion."""
        lon0, lat0, span = cam
        kx = w / span
        a = np.asarray(pts, np.float32)
        x = (a[:, 0] - lon0) * kx * math.cos(math.radians(lat0)) + w / 2
        y = (lat0 - a[:, 1]) * kx + h / 2
        return np.stack([x, y], axis=1)

    def _label(self, img, spr, x, y, alpha, force=False):
        """Blit a label unless it would collide with one already placed this frame (pins win: force)."""
        box = (x - 4, y - 2, spr.shape[1] + 8, spr.shape[0] + 4)
        if not force:
            for b in self._occupied:
                if not (box[0] + box[2] <= b[0] or b[0] + b[2] <= box[0] or box[1] + box[3] <= b[1]
                        or b[1] + b[3] <= box[1]):
                    return
        self._occupied.append(box)
        blit(img, spr, x, y, alpha)

    def _sprite(self, key, *args):
        if key not in self._sprites:
            self._sprites[key] = text_sprite(*args)
        return self._sprites[key]

    def render(self, t, w, h):
        cam = self.camera(t)
        self._occupied = []
        img = np.empty((h, w, 3), np.uint8)
        img[:] = STYLE.water
        def poly(rings):  # 3-bit fixed point for sub-pixel, anti-aliased edges
            return [np.round(self._project(r, cam, w, h) * (1 << 3)).astype(np.int32) for r in rings if len(r) > 2]

        focus_names = {"Pakistan"}
        for c in self.g["countries"]:
            col = STYLE.land_focus if c["name"] in focus_names else STYLE.land
            cv2.fillPoly(img, poly(c["rings"]), col, cv2.LINE_AA, shift=3)
        # province highlight for the active stop
        active = self._active_stop(t)
        hl_name = None
        if active:
            hl_name = active["name"] if active.get("kind") == "province" else self._province_of(active)
        a = 0.2 * ease((t - (active or {}).get("t", 0)) / 0.8) if hl_name else 0
        hl = None
        for p in self.g["provinces"]:
            if p["name"] == hl_name:
                hl = p
        if hl is not None and a > 0:
            overlay = img.copy()
            cv2.fillPoly(overlay, poly(hl["rings"]), STYLE.accent, cv2.LINE_AA, shift=3)
            img = cv2.addWeighted(overlay, a, img, 1 - a, 0)
        for p in self.g["provinces"]:
            cv2.polylines(img, poly(p["rings"]), True, tuple(int(c * 0.8) for c in STYLE.border), 1, cv2.LINE_AA, shift=3)
        for c in self.g["countries"]:
            cv2.polylines(img, poly(c["rings"]), True, STYLE.border, 2 if c["name"] == "Pakistan" else 1,
                          cv2.LINE_AA, shift=3)
        if hl is not None and a > 0:  # accent outline last, so national borders don't cover it
            ov = img.copy()
            cv2.polylines(ov, poly(hl["rings"]), True, STYLE.accent, 2, cv2.LINE_AA, shift=3)
            img = cv2.addWeighted(ov, a / 0.2, img, 1 - a / 0.2, 0)
        for r in self.g["rivers"]:
            for ln in r["lines"]:
                if len(ln) > 1:
                    cv2.polylines(img, poly([ln]), False, STYLE.river, 2, cv2.LINE_AA, shift=3)
        # mountain/desert region of the active stop
        if active and active.get("kind") in ("range/mtn", "desert", "valley", "plateau", "delta", "geoarea"):
            for rg in self.g["regions"]:
                if rg["name"] == active["name"]:
                    ov = img.copy()
                    cv2.fillPoly(ov, poly(rg["rings"]), STYLE.accent, cv2.LINE_AA, shift=3)
                    img = cv2.addWeighted(ov, 0.25 * ease((t - active["t"]) / 0.8), img, 1, 0)
        shown = [s for s in self.stops if t >= s.get("t", 0) + 0.9]
        layouts = [self._pin_layout(s, t - s.get("t", 0) - 0.9, cam, w, h, s is active) for s in shown]
        for lay in layouts:  # the story's places reserve their space before any context label
            self._occupied.append(lay["box"])
        self._country_labels(img, cam, w, h)
        self._context(img, cam, w, h, hl_name)
        for lay in layouts:
            self._draw_pin(img, lay)
        return img

    def _active_stop(self, t):
        act = None
        for s in self.stops:
            if t >= s.get("t", 0):
                act = s
        return act

    def _province_of(self, stop):
        pt = (stop["lon"], stop["lat"])
        for p in self.g["provinces"]:
            for r in p["rings"]:
                if cv2.pointPolygonTest(np.asarray(r, np.float32), pt, False) >= 0:
                    return p["name"]
        return None

    LABELS = [("IRAN", 58.5, 30.5), ("AFGHANISTAN", 65.5, 33.6), ("INDIA", 76.5, 25.0), ("CHINA", 80.5, 36.0),
              ("ARABIAN SEA", 64.0, 22.4), ("PAKISTAN", 70.6, 30.6)]

    def _country_labels(self, img, cam, w, h):
        size = max(14, int(h * 0.022))
        for name, lon, lat in self.LABELS:
            if cam[2] < 9 and name != "ARABIAN SEA":
                continue  # zoomed in: country labels would clutter
            x, y = self._project([(lon, lat)], cam, w, h)[0]
            spr = self._sprite(("lbl", name, size), name, "text", size, STYLE.muted, "SemiBold", 3)
            self._label(img, spr, x - spr.shape[1] / 2, y - spr.shape[0] / 2, 0.75)

    def _context(self, img, cam, w, h, hl_name):
        """Muted province names and neighbouring towns: the viewer always knows where they are."""
        stop_names = {s["name"] for s in self.stops}
        span = cam[2]
        if 6 <= span <= 30:
            size = max(13, int(h * 0.02))
            for p in self.g["provinces"]:
                if p["name"] in stop_names or p["name"] == "Islamabad Capital Territory":
                    continue
                if span < 12 and p["name"] != hl_name:
                    continue  # close in, only the province we are standing in is named
                c = gazetteer._centroid(p["rings"])
                x, y = self._project([(c["lon"], c["lat"])], cam, w, h)[0]
                bright = p["name"] == hl_name
                spr = self._sprite(("prov", p["name"], size, bright), p["name"].upper(), "text", size,
                                   STYLE.accent if bright else STYLE.muted, "SemiBold", 4)
                self._label(img, spr, x - spr.shape[1] / 2, y - spr.shape[0] / 2, 0.85 if bright else 0.45)
        if span <= 12:
            size = max(12, int(h * 0.019))
            for p in self.g["places"]:
                if p["name"] in stop_names or p["rank"] > (8 if span < 8 else 6):
                    continue
                x, y = self._project([(p["lon"], p["lat"])], cam, w, h)[0]
                if not (20 < x < w - 20 and 20 < y < h - 20):
                    continue
                spr = self._sprite(("ctx", p["name"], size), p["name"], "text", size, STYLE.muted, "Regular")
                n = len(self._occupied)
                self._label(img, spr, x + 6, y - spr.shape[0] / 2, 0.7)
                if len(self._occupied) > n:  # dot only when its name fits
                    cv2.circle(img, (int(x), int(y)), max(2, h // 300), STYLE.muted, -1, cv2.LINE_AA)

    AREA_KINDS = ("province", "country", "range/mtn", "desert", "river", "valley", "plateau", "delta", "geoarea")

    def _pin_layout(self, s, tl, cam, w, h, active):
        x, y = self._project([(s["lon"], s["lat"])], cam, w, h)[0]
        r = max(6, int(h * 0.009))
        size = max(18, int(h * (0.042 if active else 0.028)))
        spr = self._sprite(("pin", s["name"], size, active), s["name"].upper(), "display", size,
                           STYLE.paper if active else STYLE.muted, "SemiBold", 2)
        a = ease((tl - 0.1) / 0.4)
        area = s.get("kind") in self.AREA_KINDS
        if area:  # areas are named at their centre, no pin
            lx, ly = x - spr.shape[1] / 2, y - spr.shape[0] / 2
        else:
            lx = x + r * 2.2 if x + r * 2.2 + spr.shape[1] < w - 10 else x - r * 2.2 - spr.shape[1]
            ly = y - spr.shape[0] / 2
        return {"x": x, "y": y, "r": r, "tl": tl, "active": active, "area": area, "spr": spr, "a": a,
                "lx": lx, "ly": ly - (1 - a) * 10, "box": (lx - 8, ly, spr.shape[1] + 16, spr.shape[0])}

    def _draw_pin(self, img, L):
        x, y, r, tl = L["x"], L["y"], L["r"], L["tl"]
        if not L["area"]:
            pop = ease_out(tl / 0.35)
            if L["active"]:
                pulse = (tl % 1.6) / 1.6
                ov = img.copy()
                cv2.circle(ov, (int(x), int(y)), int(r + pulse * r * 4), STYLE.accent, 2, cv2.LINE_AA)
                cv2.addWeighted(ov, 1 - pulse, img, pulse, 0, img)
            cv2.circle(img, (int(x), int(y)), int(r * 1.6 * pop), STYLE.ink, -1, cv2.LINE_AA)
            cv2.circle(img, (int(x), int(y)), int(r * pop), STYLE.accent, -1, cv2.LINE_AA)
        spr, a, lx, ly = L["spr"], L["a"], L["lx"], L["ly"]
        pad = 6  # backing plate for legibility over any terrain
        x0, y0 = int(lx - pad), int(ly + spr.shape[0] * 0.12)
        x1, y1 = int(lx + spr.shape[1] + pad), int(ly + spr.shape[0] * 0.95)
        ov = img.copy()
        cv2.rectangle(ov, (x0, y0), (x1, y1), STYLE.ink, -1)
        cv2.addWeighted(ov, 0.55 * a, img, 1 - 0.55 * a, 0, img)
        blit(img, spr, lx, ly, a)


# --------------------------------------------------------------------------- cards

class YearReveal:
    def __init__(self, label, caption="", duration=4.0, turning=False):
        self.label, self.caption, self.duration, self.turning = label, caption, duration, turning

    def render(self, t, w, h):
        img = backdrop(w, h).copy()
        size = int(min(h * 0.30, w * 0.9 / max(3, len(self.label)) * 1.7))
        spr = text_sprite(self.label, "display", size, STYLE.paper, "Bold")
        a = ease_out(t / 0.6)
        x = (w - spr.shape[1]) / 2
        y = h * 0.40 - spr.shape[0] / 2 + (1 - a) * h * 0.05
        blit(img, spr, x, y, a)
        # accent rule grows from centre
        rule_w = int(w * 0.32 * ease_out((t - 0.3) / 0.5))
        col = STYLE.accent_2 if self.turning else STYLE.accent
        if rule_w > 2:
            yy = int(y + spr.shape[0] * 0.98)
            cv2.rectangle(img, (w // 2 - rule_w // 2, yy), (w // 2 + rule_w // 2, yy + max(3, h // 220)), col, -1)
        if self.caption:
            cs = int(h * 0.045)
            cap = text_sprite(self.caption, "text", cs, STYLE.muted, "SemiBold", 4)
            blit(img, cap, (w - cap.shape[1]) / 2, y + spr.shape[0] * 1.08, ease((t - 0.6) / 0.5))
        return img


class StatCounter:
    def __init__(self, value, unit, caption="", duration=4.0):
        self.raw = value
        self.unit, self.caption, self.duration = unit, caption, duration
        try:
            self.num = float(value.replace(",", ""))
        except ValueError:
            self.num = None

    def _fmt(self, v):
        if "." in self.raw:
            d = len(self.raw.split(".")[1])
            return f"{v:,.{d}f}"
        return f"{int(round(v)):,}" if "," in self.raw or self.num >= 10000 else str(int(round(v)))

    def render(self, t, w, h):
        img = backdrop(w, h, 1).copy()
        k = ease_out(t / 1.4)
        txt = self._fmt(self.num * k) if self.num is not None else self.raw
        unit = self.unit.upper().replace("PERCENT", "%").replace("PER CENT", "%")
        if unit == "%":
            txt, unit = txt + "%", ""
        size = int(min(h * 0.26, w * 1.4 / max(3, len(txt) + 1)))
        spr = text_sprite(txt, "display", size, STYLE.paper, "Bold")
        y = h * 0.42 - spr.shape[0] / 2
        blit(img, spr, (w - spr.shape[1]) / 2, y, ease(t / 0.3))
        yy = y + spr.shape[0]
        if unit:
            u = text_sprite(unit, "display", int(h * 0.06), STYLE.accent, "Medium", 4)
            blit(img, u, (w - u.shape[1]) / 2, yy, ease((t - 0.4) / 0.4))
            yy += u.shape[0]
        if self.caption:
            c = text_sprite(self.caption, "text", int(h * 0.04), STYLE.muted, "SemiBold", 3)
            blit(img, c, (w - c.shape[1]) / 2, yy + h * 0.02, ease((t - 0.8) / 0.4))
        return img


class QuoteCard:
    def __init__(self, text, attribution="", duration=5.0):
        self.text, self.attr, self.duration = text, attribution, duration

    def render(self, t, w, h):
        img = backdrop(w, h, 2).copy()
        size = int(h * 0.055)
        lines = wrap(self.text, "text", size, w * 0.78, "Medium")
        q = text_sprite("“", "display", int(h * 0.22), STYLE.accent, "Bold")
        total_h = len(lines) * size * 1.25
        y = (h - total_h) / 2
        blit(img, q, w * 0.11 - q.shape[1] / 2, y - q.shape[0] * 0.55, ease(t / 0.4))
        for i, ln in enumerate(lines):
            spr = text_sprite(ln, "text", size, STYLE.paper, "Medium")
            a = ease((t - 0.25 - i * 0.18) / 0.4)
            blit(img, spr, w * 0.11, y + i * size * 1.25 + (1 - a) * 8, a)
        if self.attr:
            spr = text_sprite("— " + self.attr.upper(), "text", int(size * 0.65), STYLE.accent, "SemiBold", 3)
            blit(img, spr, w * 0.11, y + total_h + size * 0.5, ease((t - 0.9) / 0.4))
        return img


class Timeline:
    """events: [{"label": "1876", "text": "Treaty of Kalat", "t": seconds since visual start}]"""

    def __init__(self, events, duration=8.0):
        self.events, self.duration = events, duration

    def render(self, t, w, h):
        img = backdrop(w, h, 3).copy()
        n = len(self.events)
        y = int(h * 0.55)
        x0, x1 = int(w * 0.08), int(w * 0.92)
        grow = ease_out(t / 0.8)
        cv2.line(img, (x0, y), (int(x0 + (x1 - x0) * grow), y), STYLE.border, 2, cv2.LINE_AA)
        for i, ev in enumerate(self.events):
            x = x0 + (x1 - x0) * (i + 0.5) / n
            te = t - ev.get("t", 0.4 + i * 0.6)
            a = ease(te / 0.4)
            if a <= 0:
                continue
            active = i == max(j for j, e in enumerate(self.events) if t >= e.get("t", 0.4 + j * 0.6))
            cv2.circle(img, (int(x), y), int(h * 0.012 * (1.4 if active else 1)), STYLE.accent if active else STYLE.muted,
                       -1, cv2.LINE_AA)
            ys = text_sprite(ev["label"], "display", int(h * (0.09 if active else 0.065)),
                             STYLE.paper if active else STYLE.muted, "Bold")
            blit(img, ys, x - ys.shape[1] / 2, y - ys.shape[0] - h * 0.03 - (1 - a) * 10, a)
            if ev.get("text"):
                fs = int(h * 0.038)
                for k, ln in enumerate(wrap(ev["text"], "text", fs, (x1 - x0) / n * 0.9, "SemiBold")[:3]):
                    ts = text_sprite(ln, "text", fs, STYLE.paper if active else STYLE.muted, "SemiBold")
                    blit(img, ts, x - ts.shape[1] / 2, y + h * 0.035 + k * fs * 1.25, a)
        return img


class KenBurns:
    """Still image with slow, motivated movement; optional reconstruction badge."""

    def __init__(self, path, duration, move="push_in", badge=None):
        im = cv2.cvtColor(cv2.imread(str(path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        self.im, self.duration, self.move, self.badge = im, duration, move, badge
        self._cache = {}

    def render(self, t, w, h):
        ih, iw = self.im.shape[:2]
        cover = max(w / iw, h / ih)
        k = ease(t / max(0.1, self.duration))
        z = {"push_in": lerp(1.0, 1.08, k), "pull_out": lerp(1.08, 1.0, k)}.get(self.move, 1.04)
        s = cover * z
        cw, ch = w / s, h / s
        pan = {"pan_left": lerp(0.6, 0.4, k), "pan_right": lerp(0.4, 0.6, k)}.get(self.move, 0.5)
        cx = iw * pan
        x0 = min(max(0, cx - cw / 2), iw - cw)
        y0 = (ih - ch) / 2
        M = np.array([[s, 0, -x0 * s], [0, s, -y0 * s]], np.float32)
        img = cv2.warpAffine(self.im, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        if self.badge:
            draw_badge(img, self.badge)
        return img


class VideoClip:
    """B-roll video, cover-cropped to the panel; loops if shorter than the beat."""

    def __init__(self, path, duration, fps, badge=None):
        self.path, self.duration, self.fps, self.badge = path, duration, fps, badge
        self.reader, self.size, self.last = None, None, None

    def render(self, t, w, h):
        from .media import FrameReader
        if self.reader is None or self.size != (w, h):
            self.reader = FrameReader(self.path, w, h, self.fps, cover=True)
            self.size = (w, h)
        f = self.reader.read()
        if f is None:  # loop
            self.reader.close()
            self.reader = FrameReader(self.path, w, h, self.fps, cover=True)
            f = self.reader.read()
        if f is None:
            return self.last if self.last is not None else backdrop(w, h)
        self.last = f
        if self.badge:
            f = f.copy()
            draw_badge(f, self.badge)
        return f


def draw_badge(img, label):
    h, w = img.shape[:2]
    spr = text_sprite(label, "text", max(14, int(h * 0.026)), STYLE.paper, "SemiBold", 2)
    x, y = w * 0.03, h * 0.04  # top-left: never under a pip window or lower-corner presenter
    ov = img.copy()
    cv2.rectangle(ov, (int(x - 10), int(y)), (int(x + spr.shape[1] + 6), int(y + spr.shape[0])), STYLE.ink, -1)
    cv2.addWeighted(ov, 0.6, img, 0.4, 0, img)
    blit(img, spr, x, y)


def build(visual, duration, fps):
    kind = visual["type"]
    if kind == "map":
        return MapJourney(visual["stops"], duration)
    if kind == "year":
        return YearReveal(visual["label"], visual.get("caption", ""), duration, visual.get("turning", False))
    if kind == "stat":
        return StatCounter(visual["value"], visual.get("unit", ""), visual.get("caption", ""), duration)
    if kind == "quote":
        return QuoteCard(visual["text"], visual.get("attribution", ""), duration)
    if kind == "timeline":
        return Timeline(visual["events"], duration)
    if kind == "image":
        return KenBurns(visual["path"], duration, visual.get("move", "push_in"), visual.get("badge"))
    if kind == "video":
        return VideoClip(visual["path"], duration, fps, visual.get("badge"))
    raise ValueError(f"unknown visual type {kind}")
