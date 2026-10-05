from docedit.director import direct, find_entities, sentences
from docedit.gazetteer import find_places
from tests.fixture import transcript
from docedit.cleanup import clean

FACES_RIGHT = {"step": 0.25, "boxes": [[0.62, 0.2, 0.15, 0.27]] * 400, "coverage": 1.0}


def test_gazetteer_finds_balochistan_places():
    names = [r["name"] for _, _, r in find_places("From Quetta through the Bolan Pass to Gwadar in Baluchistan")]
    assert names == ["Quetta", "Bolan Pass", "Gwadar", "Balochistan"]


def test_entities_years_stats_bce():
    s = sentences([{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in
                   enumerate("Mehrgarh dates to 7000 BCE and covers 200 hectares; in 1935 it was mapped.".split())])[0]
    kinds = {(e["kind"], e.get("label")) for e in find_entities(s)}
    assert ("year", "7000 BCE") in kinds and ("year", "1935") in kinds
    assert any(e["kind"] == "place" and e["place"]["name"] == "Mehrgarh" for e in find_entities(s))


def _plan():
    tr, dur = transcript()
    return direct(clean(tr, dur), FACES_RIGHT, title="Quetta")


def test_plan_covers_timeline_without_gaps():
    plan = _plan()
    beats = plan["beats"]
    assert beats[0]["start"] == 0 and abs(beats[-1]["end"] - plan["duration"]) < 1e-6
    for a, b in zip(beats, beats[1:]):
        assert abs(a["end"] - b["start"]) < 1e-6
        assert b["end"] - b["start"] >= 0.6


def test_plan_shows_geography_and_numbers():
    plan = _plan()
    types = [b["visual"]["type"] for b in plan["beats"] if b.get("visual")]
    assert "map" in types and "stat" in types
    stat = next(b["visual"] for b in plan["beats"] if b.get("visual", {}).get("type") == "stat")
    assert stat["caption"] == "OF PAKISTAN'S LAND"


def test_split_puts_graphics_away_from_face():
    plan = _plan()
    for b in plan["beats"]:
        if b["layout"] == "split":
            assert b["visual"]["presenter_side"] == "right"


def test_restraint():
    plan = _plan()
    caps = [t for b in plan["beats"] for t in b.get("text", []) if t["style"] != "title"]
    assert all(len(c["text"].split()) <= 4 for c in caps)
    sfx = plan["sfx"]
    assert all(b["t"] - a["t"] >= 6 for a, b in zip(sfx, sfx[1:]))
    assert plan["beats"][0]["layout"] == "presenter"  # the hook opens on the presenter


def test_urdu_transcript_triggers_maps_and_stats():
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in
             enumerate("کوئٹہ درہ بولان کے قریب ہے۔ بلوچستان پاکستان کے ۴۴ فیصد رقبے پر مشتمل ہے۔".split())]
    sents = sentences(words)
    places = [e["place"]["name"] for e in find_entities(sents[0]) if e["kind"] == "place"]
    assert places == ["Quetta", "Bolan Pass"]
    stat = [e for e in find_entities(sents[1]) if e["kind"] == "stat"][0]
    assert (stat["value"], stat["unit"]) == ("44", "percent")
