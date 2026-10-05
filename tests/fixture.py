"""Synthetic presenter footage + word-timed transcript for pipeline tests.

The 'narration' is tone bursts (one per word) so silences, cuts and ducking are real;
the transcript deliberately contains fillers, a stutter, a retake and a long pause.
"""
import subprocess
from pathlib import Path

import numpy as np

SCRIPT = [
    ("Umm,", 0.4), ("so", 0.0), ("Quetta", 0.0), ("is", 0.0), ("located", 0.0), ("in", 0.0), ("a", 0.0),
    ("valley", 0.9),  # false start ...
    ("Quetta", 0.0), ("is", 0.0), ("located", 0.0), ("in", 0.0), ("a", 0.0), ("strategically", 0.0),
    ("important", 0.0), ("valley", 0.0), ("surrounded", 0.0), ("by", 0.0), ("mountains.", 2.2),  # long pause
    ("Why", 0.0), ("here?", 0.6),
    ("For", 0.0), ("thousands", 0.0), ("of", 0.0), ("years,", 0.0), ("caravans", 0.0), ("crossed", 0.0),
    ("the", 0.0), ("the", 0.0), ("Bolan", 0.0), ("Pass", 0.0), ("into", 0.0), ("Balochistan.", 1.0),
    ("uh,", 0.5), ("In", 0.0), ("1935", 0.0), ("a", 0.0), ("terrible", 0.0), ("earthquake", 0.0),
    ("destroyed", 0.0), ("the", 0.0), ("city.", 0.8),
    ("Today", 0.0), ("Balochistan", 0.0), ("covers", 0.0), ("about", 0.0), ("44", 0.0), ("percent", 0.0),
    ("of", 0.0), ("Pakistan's", 0.0), ("land.", 0.7),
    ("And", 0.0), ("that", 0.0), ("is", 0.0), ("only", 0.0), ("the", 0.0), ("beginning", 0.0), ("of", 0.0),
    ("the", 0.0), ("story.", 0.5),
]
WORD = 0.34
GAP = 0.06


def transcript(lead=0.8):
    t, words = lead, []
    for w, pause in SCRIPT:
        words.append({"w": w, "s": round(t, 3), "e": round(t + WORD, 3), "p": 0.95})
        t += WORD + GAP + pause
    return {"language": "en", "words": words}, t + 0.8


def make(dirpath, face_image=None, w=1280, h=720, fps=25):
    dirpath = Path(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)
    tr, dur = transcript()
    sr = 48000
    audio = np.zeros(int(dur * sr), np.float32)
    for i, wd in enumerate(tr["words"]):
        s, e = int(wd["s"] * sr), int(wd["e"] * sr)
        tt = np.arange(e - s) / sr
        f = 140 + 30 * np.sin(i)
        audio[s:e] = 0.3 * np.sin(2 * np.pi * f * tt) * np.sin(np.pi * tt / (wd["e"] - wd["s"]))
    pcm = (audio * 32767).astype("<i2").tobytes()
    (dirpath / "voice.raw").write_bytes(pcm)
    if face_image:
        # presenter stand-in: still portrait right of centre over a studio-ish backdrop, gentle sway
        vf = (f"[1:v]scale=-1:{int(h * 0.75)}[f];[0:v][f]overlay=x='{int(w * 0.52)}+8*sin(t)':y={int(h * 0.14)}")
        vin = ["-loop", "1", "-i", str(face_image)]
    else:
        vf = "[0:v]null"
        vin = []
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
           f"gradients=s={w}x{h}:c0=0x3a3226:c1=0x6b5a44:x0=0:y0=0:x1={w}:y1={h}:d={dur}:r={fps}", *vin,
           "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", str(dirpath / "voice.raw"),
           "-filter_complex", vf + "[v]", "-map", "[v]", "-map", f"{2 if face_image else 1}:a",
           "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
           str(dirpath / "raw.mp4")]
    subprocess.run(cmd, check=True)
    import json
    (dirpath / "transcript.json").write_text(json.dumps(tr))
    return dirpath / "raw.mp4", dirpath / "transcript.json"


if __name__ == "__main__":
    import sys
    print(make(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
