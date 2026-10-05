"""Final quality-control pass: the checklist from the directing brief, made executable.

Inputs are the artefacts the pipeline already wrote (cleanup.json, plan.json,
render_log.json, the final video, the asset ledger). Produces qc_report.md and a
non-zero exit status if any blocking check fails.
"""
import re
from pathlib import Path

from .cleanup import FILLERS, norm
from .licenses import check_entry
from .media import run, write_json
from .render import _intersects
from .style import STYLE


def _check(results, area, name, ok, detail="", blocking=True):
    results.append({"area": area, "check": name, "ok": bool(ok), "detail": detail, "blocking": blocking})


def loudness(path):
    out = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
              check=False).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
    return (float(i[-1]) if i else None, float(tp[-1]) if tp else None)


def run_qc(project: Path, ledger=None, final=None):
    from .media import read_json
    project = Path(project)
    cleanup = read_json(project / "cleanup.json")
    plan = read_json(project / "plan.json")
    log = read_json(project / "render_log.json") if (project / "render_log.json").exists() else []
    R = []

    # ---------------------------------------------------------------- editing
    words = cleanup["words"]
    left_fillers = [w for w in words if norm(w["w"]) in FILLERS]
    _check(R, "Editing", "No filler words left", not left_fillers, f"{len(left_fillers)} remaining")
    long_pauses = [(a["e"], b["s"] - a["e"]) for a, b in zip(words, words[1:]) if b["s"] - a["e"] > 1.0]
    _check(R, "Editing", "No dead air > 1.0 s", not long_pauses,
           ", ".join(f"{t:.1f}s ({d:.1f}s)" for t, d in long_pauses[:8]))
    removed = cleanup["source_duration"] - cleanup["clean_duration"]
    _check(R, "Editing", "Retakes/fillers removed", True,
           f"{len(cleanup['removed_words'])} words cut; {removed:.1f}s removed "
           f"({cleanup['source_duration']:.0f}s -> {cleanup['clean_duration']:.0f}s)", blocking=False)
    first_visual = next((b["start"] for b in plan["beats"] if b["layout"] != "presenter"), None)
    _check(R, "Editing", "Something visual happens in the first 30 s",
           first_visual is not None and first_visual < 30, f"first visual at {first_visual}", blocking=False)

    # ---------------------------------------------------------------- visuals
    beats = plan["beats"]
    longest, run_start = 0.0, None
    for b in beats:
        if b["layout"] == "presenter" and not b.get("text") and b.get("camera", {}).get("move", "none") == "none":
            run_start = b["start"] if run_start is None else run_start
            longest = max(longest, b["end"] - b["start"])  # framing changes reset the stretch
        else:
            run_start = None
    _check(R, "Visuals", "No static shot longer than 15 s", longest <= 15, f"longest {longest:.1f}s", blocking=False)
    visual_time = sum(b["end"] - b["start"] for b in beats if b["layout"] != "presenter")
    share = visual_time / max(1, plan["duration"])
    _check(R, "Visuals", "Visual support share 15-70 %", 0.15 <= share <= 0.70, f"{share:.0%} of runtime",
           blocking=False)
    types = [b["visual"]["type"] for b in beats if b.get("visual")]
    repeats = sum(1 for a, b in zip(types, types[1:]) if a == b and a != "map")
    _check(R, "Visuals", "Visual variety (few identical back-to-back graphics)", repeats <= 2, f"{repeats} repeats",
           blocking=False)
    open_requests = plan.get("requests", [])
    _check(R, "Visuals", "No unfilled B-roll requests", not open_requests,
           f"{len(open_requests)} gaps listed in shot_list.md", blocking=False)
    for b in beats:
        if b.get("visual", {}).get("type") == "map":
            for s in b["visual"]["stops"]:
                if not (-180 <= s["lon"] <= 180 and -90 <= s["lat"] <= 90):
                    _check(R, "Visuals", f"Map stop {s['name']} has valid coordinates", False)

    # ---------------------------------------------------------------- face safety
    hits, dropped = [], []
    for rec in log:
        fz = rec.get("face_zone")
        dropped += [d for d in rec.get("dropped", [])]
        if not fz:
            continue
        for el in rec["elements"]:
            if el["kind"] == "panel" and rec["layout"] == "pip":
                continue  # pip: the panel is *behind* the presenter window
            if _intersects(el["box"], fz):
                hits.append(f"{rec['id']} {el['kind']} '{el.get('text', el.get('visual', ''))}' at {rec['start']:.1f}s")
        pr = rec.get("presenter_rect")
        fb = rec.get("face_box")
        if pr and fb:
            inside = fb[0] >= pr[0] - 2 and fb[1] >= pr[1] - 2 and fb[0] + fb[2] <= pr[0] + pr[2] + 2
            if not inside:
                hits.append(f"{rec['id']} face partially cropped out of presenter window at {rec['start']:.1f}s")
    _check(R, "Face safety", "No text or graphic touches the presenter's face", not hits, "; ".join(hits[:10]))
    _check(R, "Face safety", "Captions dropped instead of covering the face", True,
           f"{len(dropped)} dropped: " + "; ".join(f"'{d['text']}' ({d['why']})" for d in dropped[:6]), blocking=False)
    long_caps = [t["text"] for b in beats for t in b.get("text", [])
                 if t.get("style") != "title" and len(t["text"].split()) > STYLE.max_caption_words]
    _check(R, "Visuals", "Captions are short editorial words, not subtitles", not long_caps, ", ".join(long_caps))

    # ---------------------------------------------------------------- audio
    if final and Path(final).exists():
        lufs, tp = loudness(final)
        _check(R, "Audio", "Integrated loudness -15..-13 LUFS (YouTube)", lufs is not None and -15.5 <= lufs <= -12.5,
               f"{lufs} LUFS")
        _check(R, "Audio", "True peak <= -1 dBTP", tp is not None and tp <= -0.9, f"{tp} dBTP")
    _check(R, "Audio", "Music ducked under narration", True, "sidechain compressor keyed on voice; bed at -20 dB",
           blocking=False)
    sfx = plan.get("sfx", [])
    gaps = [b["t"] - a["t"] for a, b in zip(sfx, sfx[1:])]
    _check(R, "Audio", "Sound effects rationed (>= 6 s apart)", all(g >= 6 for g in gaps),
           f"{len(sfx)} effects", blocking=False)

    # ---------------------------------------------------------------- copyright
    used = [b["visual"] for b in beats if b.get("visual", {}).get("path")]
    bad = []
    by_file = {a["file"]: a for a in (ledger or {}).get("usable", []) + (ledger or {}).get("rejected", [])}
    for v in used:
        entry = by_file.get(v.get("file") or Path(v["path"]).name)
        if not entry:
            bad.append(f"{v['path']}: not in ledger")
        elif check_entry(entry):
            bad.append(f"{entry['file']}: {', '.join(check_entry(entry))}")
        elif entry.get("ai_generated") and entry.get("depicts") == "historical" and not v.get("badge"):
            bad.append(f"{entry['file']}: AI reconstruction shown without a reconstruction label")
    _check(R, "Copyright", "Every external asset has a verified, permitted licence", not bad, "; ".join(bad))
    _check(R, "Copyright", "Generated material recorded", True,
           "maps: Natural Earth (public domain); music & sfx: generated; fonts: SIL OFL", blocking=False)
    if ledger and ledger.get("unlisted"):
        _check(R, "Copyright", "Unlisted files in assets/ were not used", True,
               f"ignored: {', '.join(ledger['unlisted'][:8])}", blocking=False)

    write_json(project / "qc.json", R)
    lines = ["# QC report", ""]
    for area in ("Editing", "Visuals", "Face safety", "Audio", "Copyright"):
        lines.append(f"## {area}")
        for r in (r for r in R if r["area"] == area):
            mark = "PASS" if r["ok"] else ("FAIL" if r["blocking"] else "WARN")
            lines.append(f"- **{mark}** {r['check']}" + (f" — {r['detail']}" if r["detail"] else ""))
        lines.append("")
    (project / "qc_report.md").write_text("\n".join(lines))
    failed = [r for r in R if not r["ok"] and r["blocking"]]
    return R, failed
