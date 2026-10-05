"""The auto-director: transcript -> editable edit plan (plan.json).

It reads the clean narration sentence by sentence and asks, like a documentary
director would: *can this be shown?*  Places become map journeys, dates become
reveals or timelines, numbers become animated statistics, quotes become quote
cards, matching licensed assets become B-roll. Everything else stays on the
presenter, with framing changes that hide jump cuts and stress key lines.

Restraint is built in: a minimum presenter "breathing" gap between visuals, no
caption longer than four words, sound effects rationed, music sections by mood.
The output is a starting cut meant to be reviewed and rewritten (by a person or by
Claude reading the transcript) before render -- see docs/WORKFLOW.md.
"""
import re

from . import gazetteer
from .cleanup import DIGITS, SENT_END, norm
from .faces import face_span
from .style import STYLE

YEAR = re.compile(r"\b(1[0-9]{3}|20[0-4][0-9])\b(?!\s*(%|percent|km|kilomet|miles|people|metres|meters))", re.I)
BCE = re.compile(r"\b(\d{1,5})\s*(BC|BCE|B\.C\.)", re.I)
CENTURY = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\s+century\b", re.I)
STAT = re.compile(r"\b(\d[\d,\.]*)\s*(%|percent|per cent|million|billion|thousand|lakh|crore|km|kilometres|kilometers|"
                  r"square kilometres|miles|metres|meters|feet|years old|years|people|"
                  r"فیصد|ملین|ارب|ہزار|لاکھ|کروڑ|کلومیٹر|میل|سال|لوگ)(?!\w)", re.I)
URDU_UNITS = {"فیصد": "percent", "ملین": "million", "ارب": "billion", "ہزار": "thousand", "لاکھ": "lakh",
              "کروڑ": "crore", "کلومیٹر": "km", "میل": "miles", "سال": "years", "لوگ": "people"}
QUOTE = re.compile(r"[\"“]([^\"”]{12,140})[\"”]")
EMPHASIS = {"never", "only", "first", "last", "most", "greatest", "largest", "oldest", "why", "imagine", "secret",
            "forgotten", "mystery", "but", "however", "turning", "everything", "nothing"}
TURNING = {"war", "earthquake", "independence", "partition", "conquest", "invasion", "revolt", "massacre", "flood",
           "treaty", "accession", "collapse", "destroyed",
           "زلزلہ", "جنگ", "آزادی", "تقسیم", "حملہ", "بغاوت", "سیلاب", "معاہدہ", "تباہ"}
MOODS = [
    ("tension", {"war", "battle", "earthquake", "invasion", "revolt", "conflict", "destroyed", "killed", "massacre",
                 "flood", "crisis", "siege"}),
    ("emotional", {"loss", "mother", "poet", "poetry", "love", "grief", "memory", "home", "exile", "song"}),
    ("cultural", {"culture", "music", "dance", "tradition", "festival", "embroidery", "dress", "food", "language",
                  "literature", "craft", "heritage", "art"}),
    ("discovery", {"discovered", "excavation", "archaeolog", "ancient", "civilization", "civilisation", "found",
                   "mystery", "ruins", "site"}),
    ("journey", {"mountain", "valley", "desert", "coast", "road", "route", "river", "travel", "pass", "city"}),
]

MIN_PRESENTER_GAP = 3.0   # seconds of presenter between visual beats: let the human breathe
MIN_GAP_KEY_FACT = 2.0    # ...relaxed when the visual *is* the point (a number, a date)
MIN_VISUAL = 3.2
MAX_VISUAL = 9.0
MAX_STATIC = 12.0         # longest presenter stretch without a framing change
SFX_SPACING = 8.0


def sentences(words, gap=0.7):
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or SENT_END.search(w["w"]) or nxt["s"] - w["e"] > gap:
            out.append({"start": cur[0]["s"], "end": cur[-1]["e"], "words": cur,
                        "text": " ".join(x["w"] for x in cur),
                        "pause_after": round((nxt["s"] - w["e"]) if nxt else 0, 3)})
            cur = []
    return out


def _word_time(sent, token_index):
    """Time of the n-th normalised token in a sentence."""
    toks = [w for w in sent["words"] if norm(w["w"])]
    return toks[min(token_index, len(toks) - 1)]["s"] if toks else sent["start"]


def find_entities(sent):
    text = sent["text"].translate(DIGITS)
    ents = []
    for i0, i1, rec in gazetteer.find_places(text):
        ents.append({"kind": "place", "t": _word_time(sent, i0), "place": rec})
    for m in YEAR.finditer(text):
        ents.append({"kind": "year", "t": _char_time(sent, m.start()), "label": m.group(1)})
    for m in BCE.finditer(text):
        ents.append({"kind": "year", "t": _char_time(sent, m.start()), "label": f"{m.group(1)} BCE"})
    for m in CENTURY.finditer(text):
        ents.append({"kind": "year", "t": _char_time(sent, m.start()),
                     "label": f"{m.group(1)}{m.group(2).upper()} CENTURY"})
    for m in STAT.finditer(text):
        if YEAR.fullmatch(m.group(1)) and m.group(2).lower() in ("years",):
            continue
        ents.append({"kind": "stat", "t": _char_time(sent, m.start()), "value": m.group(1),
                     "unit": URDU_UNITS.get(m.group(2), m.group(2))})
    for m in QUOTE.finditer(text):
        ents.append({"kind": "quote", "t": sent["start"], "text": m.group(1).strip()})
    # a year inside a stat ("5000 years old") is not a date
    stat_ts = {e["t"] for e in ents if e["kind"] == "stat"}
    return [e for e in ents if not (e["kind"] == "year" and e["t"] in stat_ts)]


def _char_time(sent, char_pos):
    pos = 0
    for w in sent["words"]:
        if pos + len(w["w"]) >= char_pos:
            return w["s"]
        pos += len(w["w"]) + 1
    return sent["end"]


def mood_of(text):
    t = text.lower()
    best, score = "documentary", 0
    for mood, keys in MOODS:
        s = sum(1 for k in keys if k in t)
        if s > score:
            best, score = mood, s
    return best


def _emphatic(sent):
    toks = {norm(w["w"]) for w in sent["words"]}
    return sent["text"].rstrip().endswith(("!", "?")) or len(toks & EMPHASIS) >= 1


def _match_assets(sent, assets):
    toks = {norm(w["w"]) for w in sent["words"]}
    best, score = None, 0
    for a in assets:
        if a.get("kind", "image") not in ("image", "video"):
            continue
        s = len(toks & {t.lower() for t in a.get("tags", [])})
        if s > score:
            best, score = a, s
    return best if score >= 1 else None


def _side_for(faces, t0, t1):
    """Where is the presenter? Graphics go on the other side."""
    x, y, w, h = face_span(faces, t0, t1)
    cx = x + w / 2
    return "left" if cx < 0.5 else "right"


def _visual_for(sent, ents, assets, used_assets, faces, shown_places=()):
    """Pick the single strongest visual idea for a sentence (or None).

    Priority: a statistic, then a turning-point date, then a place not yet shown on a
    map, then matching B-roll, then any date, then a quotation. A number or date is
    usually the *point* of a sentence; a place already on screen earlier is context.
    """
    places = [e for e in ents if e["kind"] == "place" and e["place"]["name"] not in shown_places]
    years = [e for e in ents if e["kind"] == "year"]
    stats = [e for e in ents if e["kind"] == "stat"]
    quotes = [e for e in ents if e["kind"] == "quote"]
    turning = bool({norm(w["w"]) for w in sent["words"]} & TURNING)
    dur = sent["end"] - sent["start"]

    if stats:
        st = stats[0]
        return {"type": "stat", "value": st["value"], "unit": st["unit"], "caption": _stat_caption(sent, st)}, \
            "split", st["t"], f"statistic: {st['value']} {st['unit']}"
    if years and turning:
        y = years[0]
        return {"type": "year", "label": y["label"], "caption": _keyword_caption(sent, exclude={y["label"]}),
                "turning": True}, "split", y["t"], f"turning point: {y['label']}"
    if places:
        stops = []
        for p in places:
            if not stops or stops[-1]["name"] != p["place"]["name"]:
                stops.append({"name": p["place"]["name"], "lon": p["place"]["lon"], "lat": p["place"]["lat"],
                              "kind": p["place"]["kind"], "t": round(p["t"], 3), "bbox": p["place"].get("bbox")})
        layout = "full" if dur >= 4.5 or len(stops) > 1 else "split"
        return {"type": "map", "stops": stops, "label": stops[-1]["name"].upper()}, layout, places[0]["t"], \
            f"geography: {', '.join(s['name'] for s in stops)}"
    asset = _match_assets(sent, [a for a in assets if a["file"] not in used_assets])
    if asset:
        layout = "full" if dur >= 4 else "split"
        v = {"type": asset.get("kind", "image"), "file": asset["file"], "path": asset["path"],
             "license": asset["license"], "move": "push_in"}
        if asset.get("ai_generated") and asset.get("depicts") == "historical":
            v["badge"] = "AI RECONSTRUCTION"
        used_assets.add(asset["file"])
        return v, layout, sent["start"], f"B-roll match: {asset['file']} ({', '.join(asset.get('tags', []))})"
    if years:
        y = years[0]
        return {"type": "year", "label": y["label"], "caption": _keyword_caption(sent, exclude={y["label"]}),
                "turning": False}, "split", y["t"], f"date: {y['label']}"
    if quotes:
        return {"type": "quote", "text": quotes[0]["text"]}, "split", sent["start"], "quotation"
    return None


def _stat_caption(sent, st, n=4):
    """The words right after the number usually say what it measures: '44 percent | OF PAKISTAN'S LAND'."""
    toks = [w["w"].strip(".,;:!?\"“”") for w in sent["words"]]
    unit_toks = st["unit"].lower().split()
    for i, tk in enumerate(toks):
        if tk.replace(",", "") == st["value"].replace(",", "") and i + 1 < len(toks):
            j = i + 1 + len(unit_toks)
            tail = [x for x in toks[j:j + n] if x]
            if tail and all(_latin(x) for x in tail):
                return " ".join(tail).upper()
    return _keyword_caption(sent, exclude={st["value"], st["unit"]})


STOPWORDS = set("a an the of in on at to and or but is was were are be been it its this that these those he she they "
                "we you i his her their our my your as by for from with into over under than then so very just about "
                "there here which who whom what when where how also had has have did do does not no yes more less "
                "one two three around nearly almost some many".split())


def _keyword_caption(sent, exclude=(), n=3):
    """Up to n meaningful words: an editorial caption, never a subtitle."""
    excl = {norm(x) for x in exclude}
    toks = [norm(w["w"]) for w in sent["words"]]
    words = [t for t in toks if t and t not in STOPWORDS and t not in excl and not t.isdigit() and len(t) > 3
             and _latin(t)]
    return " ".join(words[:n]).upper() if words else ""


def _latin(s):
    """On-screen captions are English (see brief); non-Latin words are left for a human/Claude to translate."""
    return all(ord(c) < 0x250 for c in s)


def _framing_cycle():
    while True:
        yield from ("medium", "close", "medium", "wide")


def direct(clean, faces, assets=(), title=None):
    words = clean["words"]
    sents = sentences(words)
    total = clean["clean_duration"]
    cut_points = _cut_points(clean)
    beats, requests, sfx = [], [], []
    used_assets = set()
    t = 0.0
    last_visual_end = -1e9
    last_visual_type = None
    shown_places = set()
    framing = _framing_cycle()

    def presenter(t0, t1, reason, move="none", frame=None):
        if t1 - t0 < 0.05:
            return
        # split long presenter stretches at cut points / sentence starts, alternating framing
        bounds = [t0] + [c for c in cut_points if t0 + 2.0 < c < t1 - 2.0] + [t1]
        cur = t0
        for b in bounds[1:]:
            if b - cur < 2.0 and b != t1:
                continue
            beats.append({"start": round(cur, 3), "end": round(b, 3), "layout": "presenter",
                          "camera": {"framing": frame or next(framing), "move": move}, "reason": reason})
            frame = None
            cur = b

    for si, s in enumerate(sents):
        ents = find_entities(s)
        choice = _visual_for(s, ents, list(assets), used_assets, faces, shown_places) if ents or assets else None
        start = max(t, (choice[2] - 0.25) if choice else s["start"])
        if si == 0:
            start = max(start, min(1.5, s["end"] - MIN_VISUAL))  # first, the presenter's face sets the hook
        gap_needed = MIN_GAP_KEY_FACT if choice and choice[0]["type"] in ("stat", "year") else MIN_PRESENTER_GAP
        allowed = choice and start - last_visual_end >= gap_needed and s["end"] - start >= 1.5
        if choice and choice[0]["type"] == "map" and last_visual_type == "map" and start - last_visual_end < 1.5:
            allowed = False  # avoid map-after-map stutter; one journey per idea
        if allowed:
            v, layout, at, why = choice
            end = min(total, max(start + MIN_VISUAL, s["end"] + 0.35))
            end = min(end, start + MAX_VISUAL)
            # never end a visual mid-word: snap to the nearest word end
            end = _snap_end(words, end)
            presenter(t, start, "presenter delivers context", frame=None)
            if layout == "split":
                v["presenter_side"] = _side_for(faces, start, end)
            beats.append({"start": round(start, 3), "end": round(end, 3), "layout": layout,
                          "camera": {"framing": "medium", "move": "none"}, "visual": v, "reason": why})
            if layout == "full" or (v["type"] == "year" and v.get("turning")):
                if not sfx or start - sfx[-1]["t"] >= SFX_SPACING:
                    sfx.append({"t": round(start, 3), "kind": "impact" if v["type"] == "year" else "whoosh"})
            t = end
            last_visual_end, last_visual_type = end, v["type"]
            if v["type"] == "map":
                shown_places.update(st["name"] for st in v["stops"])
        else:
            if choice:
                v = choice[0]
                text = v.get("label") or (v.get("value", "") + " " + v.get("unit", "")).strip()
                if text and v["type"] in ("map", "year", "stat"):
                    s.setdefault("callouts", []).append(
                        {"text": text.upper()[:28], "start": round(max(choice[2], t), 3),
                         "end": round(min(total, max(choice[2] + STYLE.min_text_hold + 0.6, s["end"])), 3),
                         "style": "label" if v["type"] == "map" else "callout"})
            if s["start"] >= t:
                emph = _emphatic(s) and s["end"] - s["start"] > 1.2
                if emph:
                    presenter(t, s["start"], "presenter", frame=None)
                    beats.append({"start": round(s["start"], 3), "end": round(s["end"], 3), "layout": "presenter",
                                  "camera": {"framing": "close", "move": "push_in"},
                                  "reason": "emphasis: presenter owns the screen"})
                    t = s["end"]
        if si == len(sents) - 1:
            presenter(t, total, "presenter")
            t = total

    beats = _merge_tiny(_attach_callouts(beats, sents), total)
    beats = _limit_static(beats, sents, requests)
    if title:
        _add_title(beats, sents, title)
    sections = _sections(sents, total)
    music = [{"start": sec["start"], "end": sec["end"], "mood": sec["mood"]} for sec in sections]
    if sents and music:
        music[0]["start"] = round(min(sents[0]["end"], 6.0), 3)  # cold open on voice alone, then music enters
    for i, b in enumerate(beats):
        b["id"] = f"b{i:03d}"
    return {
        "version": 1,
        "duration": total,
        "title": title,
        "hook_candidates": _hooks(sents, total),
        "sections": sections,
        "beats": beats,
        "music": music,
        "sfx": sfx,
        "requests": requests,
    }


def _snap_end(words, t):
    for w in words:
        if w["s"] < t < w["e"]:
            return w["e"] + 0.05
    return t


def _cut_points(clean):
    acc, pts = 0.0, []
    for s, e in clean["keep"][:-1]:
        acc += e - s
        pts.append(round(acc, 3))
    return pts


def _attach_callouts(beats, sents):
    """Label-style text (place names, dates) on presenter beats, only one at a time."""
    calls = [c for s in sents for c in s.get("callouts", [])]
    for c in calls:
        for b in beats:
            if b["layout"] == "presenter" and b["start"] <= c["start"] < b["end"] - 1.0:
                c["end"] = round(min(c["end"], b["end"]), 3)
                if c["end"] - c["start"] >= STYLE.min_text_hold and not b.get("text"):
                    b["text"] = [c]
                break
    return beats


def _merge_tiny(beats, total, tiny=0.6):
    """Close holes and fold sub-second presenter slivers into a neighbouring presenter beat."""
    beats = sorted(beats, key=lambda b: b["start"])
    for a, b in zip(beats, beats[1:]):
        if b["start"] > a["end"] + 1e-3:
            a["end"] = b["start"]
    if beats:
        beats[0]["start"] = 0.0
        beats[-1]["end"] = total
    out = []
    for i, b in enumerate(beats):
        short = b["end"] - b["start"] < tiny and b["layout"] == "presenter"
        if short and out and out[-1]["layout"] == "presenter":
            out[-1]["end"] = b["end"]
        elif short and i + 1 < len(beats) and beats[i + 1]["layout"] == "presenter":
            beats[i + 1]["start"] = b["start"]
        elif short and out:
            out[-1]["end"] = b["end"]          # let the visual hold a few frames longer
        else:
            out.append(b)
    return out


def _limit_static(beats, sents, requests):
    """Flag long presenter stretches: framing changes are already there; ask for B-roll."""
    run_start, run_len = None, 0.0
    for b in beats:
        if b["layout"] == "presenter":
            run_start = b["start"] if run_start is None else run_start
            run_len = b["end"] - run_start
            if run_len > MAX_STATIC * 1.5:
                text = " ".join(s["text"] for s in sents if run_start <= s["start"] < b["end"])
                requests.append({"at": round(run_start, 2), "until": round(b["end"], 2), "need": "b-roll",
                                 "narration": text[:400],
                                 "suggest": _broll_suggestion(text),
                                 "why": f"{run_len:.0f}s of presenter with no visual support"})
                run_start = None
        else:
            run_start = None
    return beats


def _broll_suggestion(text):
    kw = _keyword_caption({"words": [{"w": w} for w in text.split()]}, n=6).lower()
    return {"search": kw, "ai_prompt": f"Cinematic documentary still, natural light, realistic, culturally and "
                                       f"historically accurate depiction of: {kw}. No text, no logos."}


def _add_title(beats, sents, title):
    """Show the title once, early, in the safe zone, on the first presenter beat after the hook."""
    if not sents:
        return
    t0 = sents[0]["end"] + 0.2 if len(sents) > 1 else 0.5
    for b in beats:
        if b["layout"] != "presenter" or b.get("text") or b["end"] <= t0:
            continue
        start = max(b["start"] + 0.3, t0)
        if b["end"] - start >= 2.5:
            b["text"] = [{"text": title.upper(), "start": round(start, 3), "end": round(min(b["end"], start + 3.5), 3),
                          "style": "title"}]
            return


def _sections(sents, total):
    secs, cur = [], []
    for s in sents:
        cur.append(s)
        if s["pause_after"] >= 0.7 and cur[-1]["end"] - cur[0]["start"] > 25:
            secs.append(cur)
            cur = []
    if cur:
        secs.append(cur)
    out = []
    for i, ss in enumerate(secs):
        out.append({"start": round(ss[0]["start"] if i else 0.0, 3),
                    "end": round(secs[i + 1][0]["start"] if i + 1 < len(secs) else total, 3),
                    "mood": mood_of(" ".join(s["text"] for s in ss)),
                    "first_line": ss[0]["text"][:80]})
    return out


def _hooks(sents, total):
    cands = []
    for s in sents:
        score = 0
        txt = s["text"]
        score += 2 if txt.rstrip().endswith("?") else 0
        score += 2 if STAT.search(txt) or YEAR.search(txt) else 0
        score += len({norm(w["w"]) for w in s["words"]} & EMPHASIS)
        if score >= 3:
            cands.append({"start": s["start"], "end": s["end"], "text": txt, "score": score})
    return sorted(cands, key=lambda c: -c["score"])[:5]
