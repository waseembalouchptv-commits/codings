"""Word-level transcription.

Primary path: faster-whisper with word timestamps (runs locally, no upload).
Fallback: import an existing transcript (JSON words list or SRT) so the rest of the
pipeline works with transcripts from any tool.
"""
import re
from pathlib import Path

from .media import extract_audio, read_json


def transcribe(video, workdir, model="large-v3", language=None):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise SystemExit("faster-whisper is not installed: pip install faster-whisper, "
                         "or pass --transcript with an existing transcript") from e
    wav = extract_audio(video, Path(workdir) / "audio16k.wav")
    m = WhisperModel(model, device="auto", compute_type="auto")
    # Keep disfluencies: we want to *see* the ums and restarts so cleanup can remove them.
    segments, info = m.transcribe(str(wav), language=language, word_timestamps=True, vad_filter=False,
                                  condition_on_previous_text=False,
                                  initial_prompt="Umm, uh, so... I mean, the the history of Balochistan.")
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3),
                          "p": round(w.probability, 3)})
    return {"language": info.language, "words": [w for w in words if w["w"]]}


def _srt_time(t):
    h, m, rest = t.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def load_transcript(path):
    """Accept {"words":[{w,s,e}]} JSON, a bare list of words, or an SRT (word times interpolated)."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = read_json(path)
        words = data["words"] if isinstance(data, dict) else data
        return {"language": data.get("language") if isinstance(data, dict) else None,
                "words": [{"w": w.get("w") or w.get("word"), "s": float(w.get("s", w.get("start"))),
                           "e": float(w.get("e", w.get("end"))), "p": float(w.get("p", 1.0))} for w in words]}
    if path.suffix.lower() == ".srt":
        words = []
        blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
        for b in blocks:
            lines = b.strip().splitlines()
            tl = next((l for l in lines if "-->" in l), None)
            if not tl:
                continue
            s, e = (_srt_time(x.strip()) for x in tl.split("-->"))
            text = " ".join(lines[lines.index(tl) + 1:]).split()
            if not text:
                continue
            step = (e - s) / len(text)
            for i, w in enumerate(text):  # approximate: SRT has no word timing
                words.append({"w": w, "s": round(s + i * step, 3), "e": round(s + (i + 1) * step, 3), "p": 0.5})
        return {"language": None, "words": words, "approximate_timing": True}
    raise SystemExit(f"unsupported transcript format: {path}")
