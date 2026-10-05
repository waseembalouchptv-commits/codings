"""Place lookup for map beats: Natural Earth cities/provinces/regions + curated heritage sites."""
import re
from functools import lru_cache

from .media import read_json
from .style import DATA


@lru_cache(maxsize=1)
def geo():
    return read_json(DATA / "geo_region.json")


@lru_cache(maxsize=1)
def index():
    """normalised name -> place record {name, kind, lon, lat, ...}"""
    extra = read_json(DATA / "places_extra.json")
    idx = {}

    def add(key, rec):
        k = _norm(key)
        if k and len(k) > 2 and k not in idx:
            idx[k] = rec

    g = geo()
    for p in extra["places"]:
        rec = dict(p, source="curated (approximate)")
        add(p["name"], rec)
        for a in p.get("aliases", []):
            add(a, rec)
    for p in g["provinces"]:
        rec = {"name": p["name"], "kind": "province", "country": "Pakistan", "source": "Natural Earth"}
        rec.update(_centroid(p["rings"]))
        add(p["name"], rec)
    for p in sorted(g["places"], key=lambda p: p["rank"]):
        add(p["name"], dict(p, source="Natural Earth"))
    for r in g["regions"]:
        add(r["name"], {"name": r["name"], "kind": r["kind"].lower(), "lon": r["lon"], "lat": r["lat"],
                        "source": "Natural Earth"})
    for r in g["rivers"]:
        pts = [pt for ln in r["lines"] for pt in ln]
        if pts:
            mid = pts[len(pts) // 2]
            add(r["name"], {"name": r["name"], "kind": "river", "lon": mid[0], "lat": mid[1], "source": "Natural Earth"})
    for c in g["countries"]:
        rec = {"name": c["name"], "kind": "country", "source": "Natural Earth"}
        rec.update(_centroid(c["rings"]))
        add(c["name"], rec)
    # aliases point at already-indexed canonical names
    for canon, aliases in extra.get("aliases", {}).items():
        rec = idx.get(_norm(canon))
        if rec:
            for a in aliases:
                idx.setdefault(_norm(a), rec)
    return idx


def _norm(s):
    """Lower-case, punctuation-free; keeps Urdu/Arabic letters so Urdu transcripts match too."""
    s = re.sub(r"[\u064B-\u065F\u0670\u200c\u200d]", "", s)  # harakat and joiners
    return " ".join(re.sub(r"[^\w ]+", " ", s.lower().replace("-", " ").replace("_", " ")).split())


def _centroid(rings):
    """Area-weighted centroid of the largest ring (vertex averages drift toward detailed coasts)."""
    big = max(rings, key=len)
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(big, big[1:] + big[:1]):
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return {"lon": round(cx / (3 * a), 3), "lat": round(cy / (3 * a), 3),
            "bbox": [min(xs), min(ys), max(xs), max(ys)]}


# Index keys that are also ordinary English words, so never treated as a place mention
STOP = frozenset({"bat", "most", "mardan king", "nice", "reading", "victoria", "hub"})


def find_places(text):
    """Return [(start_char, end_char, record)] for place mentions, longest match first."""
    idx = index()
    t = _norm(text)
    tokens = t.split()
    found, used = [], set()
    for n in (4, 3, 2, 1):
        for i in range(len(tokens) - n + 1):
            if any(j in used for j in range(i, i + n)):
                continue
            key = " ".join(tokens[i:i + n])
            rec = idx.get(key)
            if rec and key not in STOP:
                found.append((i, i + n, rec))
                used.update(range(i, i + n))
    return sorted(found, key=lambda f: f[0])
