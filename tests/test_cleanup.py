from docedit.cleanup import clean, detect_removals, remap


def W(text, start=0.0, step=0.4, gaps=None):
    gaps = gaps or {}
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3)})
        t += step + gaps.get(i, 0.0)
    return out


def kept_text(result):
    return " ".join(w["w"] for w in result["words"])


def test_fillers_and_stutters_removed():
    words = W("So um the the city is old.")
    drop = detect_removals(words)
    assert drop[1] == "filler"
    assert drop[2] == "stutter" and 3 not in drop


def test_retake_keeps_later_take():
    words = W("Quetta is located in a valley Quetta is located in a strategically important valley.", gaps={5: 1.0})
    res = clean({"words": words}, 30)
    assert kept_text(res) == "Quetta is located in a strategically important valley."
    assert {r["why"] for r in res["removed_words"]} == {"retake"}


def test_false_start_with_discourse_marker():
    words = W("so Quetta is Quetta is the capital.", gaps={2: 0.6})
    res = clean({"words": words}, 30)
    assert kept_text(res) == "Quetta is the capital."


def test_distinct_sentences_untouched():
    words = W("The fort was built in 1876. The bazaar grew around it.", gaps={5: 0.5})
    res = clean({"words": words}, 30)
    assert kept_text(res) == "The fort was built in 1876. The bazaar grew around it."
    assert not res["removed_words"]


def test_long_pause_tightened_and_timeline_consistent():
    words = W("History matters. Because memory fades.", gaps={1: 3.0})
    res = clean({"words": words}, 30)
    assert res["tightened_pauses"]
    gap = res["words"][2]["s"] - res["words"][1]["e"]
    assert gap < 1.0
    assert abs(res["clean_duration"] - sum(e - s for s, e in res["keep"])) < 1e-6
    # clean word times are monotonic and inside the clean timeline
    ts = [w["s"] for w in res["words"]]
    assert ts == sorted(ts) and ts[-1] < res["clean_duration"]


def test_remap_outside_ranges():
    assert remap(0.5, [[1, 2], [3, 4]]) is None
    assert remap(3.5, [[1, 2], [3, 4]]) == 1.5


def test_cold_open_plays_hook_first_and_keeps_it_in_place():
    words = W("Balochistan is vast. Why does nobody know it?", gaps={2: 0.8})
    res = clean({"words": words}, 30, cold_open=[words[3]["s"] - 0.05, words[-1]["e"] + 0.05])
    texts = [w["w"] for w in res["words"]]
    assert texts[:5] == ["Why", "does", "nobody", "know", "it?"]
    assert texts[5:] == "Balochistan is vast. Why does nobody know it?".split()
