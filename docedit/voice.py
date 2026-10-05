"""Voice restoration: measure the room, then remove what doesn't belong.

Runs on the *source* audio before any cuts, so every kept word gets the same
treatment and the noise profile can be learned from the real pauses.

Measured from the recording (using the transcript to know where speech is):
  * noise floor in pauses and signal-to-noise ratio
  * mains hum (50 Hz in Pakistan, 60 Hz elsewhere) and its harmonics
  * impulsive clicks/pops in pauses (mouth clicks, chair, cable)
  * sibilance (harsh "s" energy)

Then a chain is built only from what is needed:
  high-pass (rumble, AC, traffic) -> hum notches -> RNNoise speech denoiser
  (Xiph model, BSD) -> spectral denoise at the measured floor -> declick ->
  de-ess -> gentle downward expander so room tone sinks between words.
Strength scales with how noisy the room is; a clean room is barely touched,
because over-processing makes voices sound robotic.
"""
import wave

import numpy as np

from .media import run
from .style import DATA

SR = 48000
RNN_MODEL = DATA / "rnnoise_std.rnnn"


def _read(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def _db(x):
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)) if len(x) else -120.0


def _spans(words, duration, pad=0.12, min_len=0.25):
    """Pause spans (no words) and speech spans, in seconds."""
    pauses, speech = [], []
    t = 0.0
    for w in sorted(words, key=lambda w: w["s"]):
        if w["s"] - pad - (t + pad) >= min_len:
            pauses.append((t + pad, w["s"] - pad))
        speech.append((w["s"], w["e"]))
        t = max(t, w["e"])
    if duration - pad - (t + pad) >= min_len:
        pauses.append((t + pad, duration - pad))
    return pauses, speech


def _cat(x, spans):
    parts = [x[int(s * SR):int(e * SR)] for s, e in spans]
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


def _band_db(spec, freqs, lo, hi):
    m = (freqs >= lo) & (freqs < hi)
    return float(10 * np.log10(np.mean(spec[m]) + 1e-12)) if m.any() else -120.0


def analyze(audio_wav, words, duration):
    x = _read(audio_wav)
    pauses, speech = _spans(words, duration)
    quiet = _cat(x, pauses)
    talk = _cat(x, speech)
    noise_db, speech_db = _db(quiet), _db(talk)
    rep = {"noise_floor_db": round(noise_db, 1), "speech_db": round(speech_db, 1),
           "snr_db": round(speech_db - noise_db, 1), "pause_seconds": round(len(quiet) / SR, 1)}

    # hum: narrow peaks at 50/60 Hz harmonics vs their neighbourhood, in the pauses
    rep["hum_hz"] = None
    if len(quiet) > SR // 2:
        n = min(len(quiet), SR * 20)
        spec = np.abs(np.fft.rfft(quiet[:n] * np.hanning(n))) ** 2
        freqs = np.fft.rfftfreq(n, 1 / SR)
        best = 0.0
        for base in (50, 60):
            prom = []
            for h in (1, 2, 3):
                f = base * h
                peak = _band_db(spec, freqs, f - 1.5, f + 1.5)
                around = (_band_db(spec, freqs, f - 12, f - 5) + _band_db(spec, freqs, f + 5, f + 12)) / 2
                prom.append(peak - around)
            score = float(np.mean(sorted(prom)[-2:]))
            if score > 10 and score > best:
                best, rep["hum_hz"] = score, base
        rep["hum_prominence_db"] = round(best, 1)

    # clicks: short impulses in the pauses, counted as events (samples within 5 ms = one click)
    if len(quiet):
        med = float(np.median(np.abs(quiet))) + 1e-9
        idx = np.flatnonzero(np.abs(quiet) > max(med * 25, 0.05))
        events = int(np.sum(np.diff(idx) > SR * 0.005)) + (1 if len(idx) else 0)
        rep["clicks_per_min"] = round(events / max(len(quiet) / SR / 60, 1 / 60), 1)
    else:
        rep["clicks_per_min"] = 0.0

    # sibilance: 5-9 kHz energy share while speaking
    if len(talk) > SR:
        n = min(len(talk), SR * 30)
        spec = np.abs(np.fft.rfft(talk[:n])) ** 2
        freqs = np.fft.rfftfreq(n, 1 / SR)
        rep["sibilance_db"] = round(_band_db(spec, freqs, 5000, 9000) - _band_db(spec, freqs, 150, 4000), 1)
    else:
        rep["sibilance_db"] = -40.0
    return rep


def chain(rep, mode="auto"):
    """Build the ffmpeg filter chain from the measurements."""
    if mode == "off":
        return "anull", ["denoise off"]
    snr, floor = rep["snr_db"], rep["noise_floor_db"]
    steps, notes = ["highpass=f=70:poles=2"], ["high-pass 70 Hz (rumble, AC, traffic)"]
    if rep.get("hum_hz"):
        f = rep["hum_hz"]
        steps += [f"bandreject=f={f * h}:width_type=q:w=25" for h in (1, 2, 3, 4)]
        notes.append(f"hum notches at {f} Hz x1-4")
    strong = mode == "strong" or snr < 25
    if mode == "strong" or snr < 40:
        mix = 0.95 if strong else (0.8 if snr < 32 else 0.6)
        steps.append(f"arnndn=m='{RNN_MODEL}':mix={mix}")
        notes.append(f"RNNoise speech denoiser (mix {mix})")
    nr = 18 if strong else (10 if snr < 40 else 5)
    nf = int(np.clip(floor + 3, -80, -20))
    steps.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
    notes.append(f"spectral denoise {nr} dB at measured floor {nf} dBFS")
    if rep.get("clicks_per_min", 0) > 4:
        steps.append("adeclick=w=55:o=75")
        notes.append("declick")
    if rep.get("sibilance_db", -40) > -14:
        steps.append("deesser=i=0.4:m=0.5:f=0.5")
        notes.append("de-ess")
    # downward expander: only room tone sits below this; speech is never touched
    thr = 10 ** ((max(floor, -75) + 10) / 20)
    steps.append(f"agate=threshold={thr:.5f}:ratio=2:range=0.25:attack=5:release=180:knee=4")
    notes.append("gentle expander between words (-12 dB max)")
    return ",".join(steps), notes


def measure_pauses(video_or_wav, words, duration, workdir):
    """Noise floor in the pauses of an already-processed file (used by QC)."""
    tmp = workdir / "_qc_voice.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", video_or_wav, "-vn", "-ac", "1", "-ar", SR, tmp])
    x = _read(tmp)
    tmp.unlink()
    pauses, _ = _spans(words, duration, pad=0.15)
    return round(_db(_cat(x, pauses)), 1)


def prepare(src_video, words, duration, workdir, mode="auto"):
    """Analyse the source audio and return (filter_chain, report)."""
    wav = workdir / "source48k.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", src_video, "-vn", "-ac", "1", "-ar", SR, wav])
    rep = analyze(wav, words, duration)
    flt, notes = chain(rep, mode)
    rep.update({"mode": mode, "chain": flt, "steps": notes})
    wav.unlink()
    return flt, rep
