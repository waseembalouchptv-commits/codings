"""Thin ffmpeg/ffprobe helpers and raw-frame pipes."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np


def run(cmd, **kw):
    kw.setdefault("check", True)
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH; install ffmpeg first")


def probe(path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path]).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    res = {"duration": float(info["format"]["duration"]), "has_audio": a is not None}
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
        rot = 0
        for sd in v.get("side_data_list", []):
            rot = int(sd.get("rotation", 0) or 0)
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) in (90, 270):
            w, h = h, w
        res.update({"width": w, "height": h, "fps": fps})
    return res


def extract_audio(src, dst, rate=16000):
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", rate, dst])
    return dst


class FrameReader:
    """Decode a video to RGB numpy frames at a fixed size/fps."""

    def __init__(self, path, width, height, fps, start=0.0, duration=None, cover=False):
        self.w, self.h = width, height
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path)]
        if duration is not None:
            cmd += ["-t", f"{duration:.3f}"]
        scale = f"scale={width}:{height}:flags=bicubic"
        if cover:  # fill the frame, crop the excess (never stretch)
            scale = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=bicubic,crop={width}:{height}"
        cmd += ["-vf", f"fps={fps},{scale}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        self.size = width * height * 3

    def read(self):
        buf = self.proc.stdout.read(self.size)
        if len(buf) < self.size:
            return None
        return np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)

    def __iter__(self):
        while (f := self.read()) is not None:
            yield f

    def close(self):
        self.proc.stdout.close()
        self.proc.wait()


class FrameWriter:
    def __init__(self, path, width, height, fps, crf=18, preset="medium"):
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
               "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
               "-pix_fmt", "yuv420p", str(path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray):
        self.proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self):
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError("ffmpeg encoder failed")


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def read_json(path):
    return json.loads(Path(path).read_text())
