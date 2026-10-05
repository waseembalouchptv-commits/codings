"""Copyright-clean sound: generated score beds and sound effects, plus the final mix.

Nothing here samples anyone else's recording. Music is synthesised from scratch
(pads, drones, plucked strings via Karplus-Strong), so it is always usable on a
monetised channel. Swap in licensed tracks by putting them in the assets ledger
with kind "music" and a mood tag -- the mixer prefers them over generated beds.
"""
import wave

import numpy as np

from .media import run

SR = 48000

# mood -> (root Hz, scale degrees in semitones for chords, character)
MOODS = {
    "documentary": (110.00, [[0, 7, 12, 16], [-3, 4, 9, 12], [-7, 0, 5, 9], [-5, 2, 7, 11]], "pad"),
    "journey":     (98.00,  [[0, 7, 14, 16], [5, 12, 16, 21], [-3, 7, 12, 16], [2, 9, 14, 17]], "pad"),
    "discovery":   (103.83, [[0, 7, 12, 14], [-4, 3, 8, 15], [-2, 5, 10, 14], [0, 7, 11, 14]], "pad"),
    "emotional":   (87.31,  [[0, 7, 12, 15], [-4, 3, 8, 12], [-7, 0, 5, 8], [-5, 2, 7, 10]], "pad"),
    "tension":     (73.42,  [[0, 1, 7, 12], [0, 6, 7, 13], [-2, 5, 10, 13], [0, 3, 7, 13]], "drone"),
    # drone on Sa/Pa with plucked phrases in a pentatonic-leaning mode (original, not a quoted melody)
    "cultural":    (130.81, [[0, 7, 12], [0, 7, 12], [0, 5, 12], [0, 7, 12]], "pluck"),
}


def _env(n, attack, release):
    e = np.ones(n, np.float32)
    a, r = int(attack * SR), int(release * SR)
    if a:
        e[:a] = np.linspace(0, 1, a) ** 2
    if r:
        e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def _lp(x, cutoff):
    """One-pole low-pass (cheap, warm)."""
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / SR)
    return lfilter([1 - a], [1, -a], x).astype(np.float32)


def _reverb(x, seconds=2.8, mix=0.35, seed=7):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    ir = rng.normal(0, 1, n).astype(np.float32) * np.exp(-np.linspace(0, 7, n)).astype(np.float32)
    ir = _lp(ir, 5000)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:len(x)].astype(np.float32)
    return x * (1 - mix) + wet * mix


def _pad_chord(freqs, dur, rng, bright=1.0):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    out = np.zeros(n, np.float32)
    for f in freqs:
        for det in (-0.12, 0.0, 0.11):           # chorus-like detune
            ff = f * 2 ** (det / 12)
            ph = rng.uniform(0, 2 * np.pi)
            for h, amp in ((1, 1.0), (2, 0.35 * bright), (3, 0.12 * bright), (4, 0.05 * bright)):
                out += amp * np.sin(2 * np.pi * ff * h * t + ph * h)
    lfo = 0.85 + 0.15 * np.sin(2 * np.pi * 0.07 * t + rng.uniform(0, 6))
    return out * lfo * _env(n, dur * 0.35, dur * 0.35) / (len(freqs) * 3)


def _pluck(f, dur, rng, decay=0.996):
    """Karplus-Strong plucked string, vectorised one period at a time."""
    n = int(dur * SR)
    period = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, period).astype(np.float32)
    blocks = []
    for _ in range(n // period + 1):
        blocks.append(buf)
        buf = decay * 0.5 * (buf + np.roll(buf, -1))
    return np.concatenate(blocks)[:n]


def score(mood, duration, seed=0):
    """Generate an original music bed for a mood (mono float32, -1..1)."""
    root, chords, kind = MOODS.get(mood, MOODS["documentary"])
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    out = np.zeros(n + SR * 8, np.float32)
    bar = 8.0 if kind != "tension" else 10.0
    t = 0.0
    i = 0
    while t < duration:
        ch = chords[i % len(chords)]
        freqs = [root * 2 ** (s / 12) for s in ch]
        seg = _pad_chord(freqs, bar + 2.0, rng, bright=0.6 if kind == "tension" else 1.0)
        s = int(t * SR)
        out[s:s + len(seg)] += seg[:len(out) - s]
        if kind == "pluck":  # sparse plucked phrase over the drone
            scale = [0, 2, 3, 7, 9, 12, 14]
            for k in range(rng.integers(3, 6)):
                at = s + int(rng.uniform(0.5, bar - 1.0) * SR)
                note = root * 2 * 2 ** (scale[rng.integers(0, len(scale))] / 12)
                p = _pluck(note, 2.5, rng) * 0.25
                out[at:at + len(p)] += p[:len(out) - at]
        t += bar
        i += 1
    # sub drone and air
    tt = np.arange(len(out), dtype=np.float32) / SR
    out += 0.18 * np.sin(2 * np.pi * root / 2 * tt) * (0.8 + 0.2 * np.sin(2 * np.pi * 0.05 * tt))
    if kind == "tension":
        out += 0.08 * np.sin(2 * np.pi * root / 2 * tt) * (np.sin(2 * np.pi * 0.9 * tt) > 0.6)  # slow pulse
    out += _lp(rng.normal(0, 0.02, len(out)).astype(np.float32), 900)
    out = _lp(out, 2600 if kind != "tension" else 1400)
    out = _reverb(out[:n], 3.5, 0.4)
    return out / (np.max(np.abs(out)) + 1e-9) * 0.8


def sfx(kind, seed=0):
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        d = 0.9
        n = int(d * SR)
        noise = rng.normal(0, 1, n).astype(np.float32)
        t = np.linspace(0, 1, n, dtype=np.float32)
        env = np.sin(np.pi * t ** 0.7) ** 2
        lo = _lp(noise, 600)
        hi = noise - _lp(noise, 2500)
        x = (lo * (1 - t) + hi * t * 0.5 + _lp(noise, 1500) * 0.6) * env
        return _reverb(x / (np.max(np.abs(x)) + 1e-9), 1.2, 0.25) * 0.6
    if kind == "impact":
        d = 2.0
        n = int(d * SR)
        t = np.arange(n, dtype=np.float32) / SR
        f = 55 * np.exp(-t * 3) + 38
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.2)
        click = _lp(rng.normal(0, 1, n).astype(np.float32), 3000) * np.exp(-t * 40) * 0.5
        x = body + click
        return _reverb(x / (np.max(np.abs(x)) + 1e-9), 2.5, 0.3) * 0.8
    if kind == "page":
        n = int(0.5 * SR)
        x = (rng.normal(0, 1, n).astype(np.float32) - _lp(rng.normal(0, 1, n).astype(np.float32), 1800))
        return x * _env(n, 0.05, 0.35) * 0.3
    raise ValueError(kind)


def write_wav(path, x, stereo=True):
    x = np.clip(x, -1, 1)
    data = (x * 32767).astype("<i2")
    if stereo:
        data = np.repeat(data[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def build_beds(plan, music_db=-20.0, sfx_db=-10.0, licensed=None):
    """Return (music, sfx) full-length mono tracks aligned to the edit timeline."""
    total = plan["duration"]
    n = int(total * SR) + SR
    music = np.zeros(n, np.float32)
    fx = np.zeros(n, np.float32)
    g_music = 10 ** (music_db / 20)
    for i, cue in enumerate(plan.get("music", [])):
        if cue.get("mood") == "silence":
            continue  # deliberate silence is an editorial choice
        dur = cue["end"] - cue["start"]
        if dur < 2:
            continue
        bed = _licensed_bed(licensed, cue["mood"], dur) if licensed else None
        if bed is None:
            bed = score(cue["mood"], dur + 2.0, seed=i)
        bed = bed[:int((dur + 1.5) * SR)]
        fade_in = 0.4 if i else 2.5
        bed = bed * _env(len(bed), fade_in, 2.0) * g_music * 10 ** (cue.get("gain_db", 0) / 20)
        s = int(cue["start"] * SR)
        music[s:s + len(bed)] += bed[:n - s]
    g_fx = 10 ** (sfx_db / 20)
    for i, e in enumerate(plan.get("sfx", [])):
        x = sfx(e["kind"], seed=i) * g_fx
        s = max(0, int((e["t"] - (0.45 if e["kind"] == "whoosh" else 0.0)) * SR))  # whoosh peaks on the cut
        fx[s:s + len(x)] += x[:n - s]
    return music, fx


def _licensed_bed(licensed, mood, dur):
    for a in licensed:
        if a.get("kind") == "music" and mood in a.get("tags", []):
            from .media import extract_audio
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
                extract_audio(a["path"], tmp.name, rate=SR)
                with wave.open(tmp.name) as w:
                    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
            reps = int(np.ceil(dur * SR / len(x))) + 1
            return np.tile(x, reps)
    return None


def mix(voice_src, music_wav, fx_wav, out_wav, target_lufs=-14.0):
    """Voice tone (noise was already removed in the clean step, see voice.py) + music ducked
    under narration + sfx, loudness-normalised for YouTube."""
    voice = ("equalizer=f=250:t=q:w=1.2:g=-2,equalizer=f=3500:t=q:w=1.0:g=2,"
             "acompressor=threshold=-20dB:ratio=3:attack=8:release=120:makeup=2")
    graph = (f"[0:a]{voice},aresample={SR},aformat=channel_layouts=stereo,asplit=2[v][key];"
             f"[1:a][key]sidechaincompress=threshold=0.03:ratio=8:attack=40:release=500[duck];"
             f"[v][duck][2:a]amix=inputs=3:normalize=0:duration=first,"
             f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11[out]")
    run(["ffmpeg", "-y", "-v", "error", "-i", voice_src, "-i", music_wav, "-i", fx_wav,
         "-filter_complex", graph, "-map", "[out]", "-ar", SR, out_wav])
    return out_wav
