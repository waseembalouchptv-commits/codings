"""Turn a raw take into one clean performance.

Works on word timestamps. Produces *keep ranges* in source time plus a log of every
removal and why, so the edit is reviewable and reversible (edit cleanup.json and
re-render; nothing is destructive).

Rules (mirroring how a human editor cuts a talking head):
  * fillers ("um", "uh" ...) removed when they stand alone; meaningful words never are
  * stutters ("the the") keep the last occurrence
  * retakes / false starts: when a phrase is restarted, the *later* take wins
  * pauses: mid-sentence gaps tightened, sentence-end breaths kept short,
    paragraph pauses (topic changes) keep a deliberate beat
"""
import difflib
import re

DISCOURSE = {"so", "and", "okay", "ok", "now", "well", "right", "actually", "basically", "like", "toh", "to", "acha"}
FILLERS = {"um", "umm", "ummm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "mm", "mhm", "eh",
           "ام", "امم", "اممم", "اوں", "ہمم", "اہ"}
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")  # Urdu/Arabic-Indic digits
SENT_END = re.compile(r"[.!?؟۔]$")


def norm(w):
    return re.sub(r"[^\w']+", "", w.lower().translate(DIGITS))


def phrases(words, gap=0.45):
    """Split into phrases at sentence punctuation or audible gaps."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or SENT_END.search(w["w"]) or nxt["s"] - w["e"] > gap:
            out.append(cur)
            cur = []
    return out


def _text(words, idx):
    toks = [norm(words[i]["w"]) for i in idx if norm(words[i]["w"]) not in FILLERS]
    while toks and toks[0] in DISCOURSE:  # "so, Quetta is..." restarts as "Quetta is..."
        toks = toks[1:]
    return " ".join(t for t in toks if t)


def detect_removals(words, retake_window=25.0, similarity=0.72):
    """Return {word_index: reason} for words to drop."""
    drop = {}
    # 1. fillers
    for i, w in enumerate(words):
        if norm(w["w"]) in FILLERS:
            drop[i] = "filler"
    # 2. stutters: immediate repeated word (keep the last one)
    for i in range(len(words) - 1):
        a, b = norm(words[i]["w"]), norm(words[i + 1]["w"])
        if a and a == b and words[i + 1]["s"] - words[i]["e"] < 0.8 and i not in drop:
            drop[i] = "stutter"
    # 3. retakes and false starts, phrase level: later take wins
    ph = phrases(words)
    for a in range(len(ph)):
        ta = _text(words, ph[a])
        if not ta:
            continue
        na = len(ta.split())
        for b in range(a + 1, len(ph)):
            if words[ph[b][0]]["s"] - words[ph[a][-1]]["e"] > retake_window:
                break
            tb = _text(words, ph[b])
            if not tb:
                continue
            nb = tb.split()
            prefix = " ".join(nb[:na])
            ratio = difflib.SequenceMatcher(None, ta, prefix).ratio()
            k = min(na, 3)
            same_start = na >= 2 and ta.split()[:k] == nb[:k]
            unfinished = not SENT_END.search(words[ph[a][-1]]["w"])
            if (na >= 3 and ratio >= similarity) or (same_start and (unfinished or ratio >= 0.5)):
                for i in ph[a]:
                    drop.setdefault(i, "retake" if ratio >= similarity else "false start")
                break
    return drop


def keep_ranges(words, drop, duration, pad=0.06, mid_gap=0.18, sent_gap=0.38, para_gap=0.75,
                para_threshold=1.4):
    """Build source-time keep ranges from surviving words, tightening pauses."""
    kept = [i for i in range(len(words)) if i not in drop]
    if not kept:
        return [], []
    ranges, pauses = [], []
    s = max(0.0, words[kept[0]]["s"] - pad)
    for a, b in zip(kept, kept[1:]):
        wa, wb = words[a], words[b]
        gap = wb["s"] - wa["e"]
        contiguous = b == a + 1
        if SENT_END.search(wa["w"]):
            target = para_gap if gap >= para_threshold else sent_gap
        else:
            target = mid_gap
        if contiguous and gap <= target + 2 * pad:
            continue  # natural flow, leave untouched
        # cut: end current range after wa (plus pad / half the allowed pause), restart before wb
        keep_pause = min(gap, target)
        e = wa["e"] + min(pad + keep_pause / 2, gap / 2)
        ns = max(e, wb["s"] - pad - keep_pause / 2)
        if not contiguous:  # never let padding leak into a removed word
            e = min(e, max(wa["e"], words[a + 1]["s"] - 0.01))
            ns = max(ns, min(wb["s"], words[b - 1]["e"] + 0.01))
        ranges.append([round(s, 3), round(e, 3)])
        s = ns
        if gap > target:
            pauses.append({"at": round(wa["e"], 3), "was": round(gap, 2), "now": round(keep_pause, 2)})
    ranges.append([round(s, 3), round(min(duration, words[kept[-1]]["e"] + 0.25), 3)])
    # merge ranges separated by less than one frame
    merged = []
    for r in ranges:
        if merged and r[0] - merged[-1][1] < 0.04:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return merged, pauses


def remap(t, ranges):
    """Source time -> clean time (None if t falls in a removed region)."""
    acc = 0.0
    for s, e in ranges:
        if t < s:
            return None
        if t <= e:
            return acc + (t - s)
        acc += e - s
    return None


def clean(transcript, duration, cold_open=None, **kw):
    """cold_open: optional [start, end] in source seconds, played first as the hook (it also stays in place)."""
    words = transcript["words"]
    drop = detect_removals(words)
    ranges, pauses = keep_ranges(words, drop, duration, **kw)
    if cold_open:
        ranges = [[round(float(cold_open[0]), 3), round(float(cold_open[1]), 3)]] + ranges
    clean_words, acc = [], 0.0
    for rs, re_ in ranges:  # walk ranges in play order: a cold open repeats its words
        for i, w in enumerate(words):
            if i not in drop and w["s"] >= rs - 1e-3 and w["e"] <= re_ + 0.05:
                clean_words.append({"w": w["w"], "s": round(acc + w["s"] - rs, 3),
                                    "e": round(acc + min(w["e"], re_) - rs, 3), "src": w["s"]})
        acc += re_ - rs
    removed = [{"i": i, "w": words[i]["w"], "t": words[i]["s"], "why": why} for i, why in sorted(drop.items())]
    return {
        "keep": ranges,
        "cold_open": cold_open,
        "clean_duration": round(sum(e - s for s, e in ranges), 3),
        "source_duration": duration,
        "removed_words": removed,
        "tightened_pauses": pauses,
        "words": clean_words,
    }
