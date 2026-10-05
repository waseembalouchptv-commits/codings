"""docedit command line.

    python -m docedit run raw.mp4 -o projects/quetta --assets assets/ --title "Quetta"

or step by step (each step reads/writes files in the project folder, so any
artefact -- especially plan.json -- can be reviewed and edited between steps):

    analyze   raw video -> transcript.json, source.json
    clean     -> cleanup.json (keep ranges + every removal and why), clean.mp4 (graded)
    faces     -> faces.json (face track on the clean take)
    plan      -> plan.json, edit_report.md, shot_list.md
    render    -> final.mp4, render_log.json, credits.txt, asset_log.json
    qc        -> qc_report.md (non-zero exit if a blocking check fails)
"""
import argparse
import sys
from pathlib import Path

from . import audio, cleanup, director, faces, licenses, qc, render, voice
from .media import probe, read_json, require_ffmpeg, write_json
from .transcribe import load_transcript, transcribe


def _p(args):
    p = Path(args.project)
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmd_analyze(args):
    p = _p(args)
    info = probe(args.video)
    info["path"] = str(Path(args.video).resolve())
    write_json(p / "source.json", info)
    if args.transcript:
        tr = load_transcript(args.transcript)
    else:
        tr = transcribe(args.video, p, model=args.model, language=args.language)
    write_json(p / "transcript.json", tr)
    print(f"transcript: {len(tr['words'])} words, source {info['duration']:.1f}s")


def cmd_clean(args):
    p = _p(args)
    src = read_json(p / "source.json")
    tr = read_json(p / "transcript.json")
    cold = [float(x) for x in args.cold_open.split("-")] if getattr(args, "cold_open", None) else None
    cl = cleanup.clean(tr, src["duration"], cold_open=cold)
    write_json(p / "cleanup.json", cl)
    grade = render.estimate_grade(src["path"])
    write_json(p / "grade.json", grade)
    vchain, vrep = voice.prepare(src["path"], tr["words"], src["duration"], p, mode=args.denoise)
    render.make_clean(src["path"], cl["keep"], p / "clean.mp4", grade, p, fps=round(src["fps"]), voice_chain=vchain)
    vrep["after_noise_floor_db"] = voice.measure_pauses(p / "clean.mp4", cl["words"], cl["clean_duration"], p)
    write_json(p / "voice.json", vrep)
    print(f"voice: noise floor {vrep['noise_floor_db']} -> {vrep['after_noise_floor_db']} dBFS "
          f"(SNR {vrep['snr_db']} dB); {'; '.join(vrep['steps'])}")
    by = {}
    for r in cl["removed_words"]:
        by[r["why"]] = by.get(r["why"], 0) + 1
    print(f"clean take: {src['duration']:.1f}s -> {cl['clean_duration']:.1f}s; removed {by}; "
          f"{len(cl['tightened_pauses'])} pauses tightened; grade {grade}")


def cmd_faces(args):
    p = _p(args)
    fz = faces.track(p / "clean.mp4")
    write_json(p / "faces.json", fz)
    print(f"faces: coverage {fz['coverage']:.0%}, usual position {fz['dominant']}")
    if fz["coverage"] < 0.5:
        print("warning: face found in <50% of samples; layouts fall back to protecting the frame centre")


def cmd_plan(args):
    p = _p(args)
    cl = read_json(p / "cleanup.json")
    fz = read_json(p / "faces.json")
    ledger = licenses.load_ledger(args.assets)
    for r in ledger["rejected"]:
        print(f"asset rejected: {r['file']}: {', '.join(r['problems'])}")
    if (p / "plan.json").exists() and not args.force:
        print("plan.json exists (edited plans are precious); use --force to regenerate")
        return
    plan = director.direct(cl, fz, ledger["usable"], title=args.title)
    write_json(p / "plan.json", plan)
    write_reports(p, plan)
    kinds = {}
    for b in plan["beats"]:
        k = b.get("visual", {}).get("type", b["layout"])
        kinds[k] = kinds.get(k, 0) + 1
    print(f"plan: {len(plan['beats'])} beats {kinds}; {len(plan['requests'])} visual requests -> shot_list.md")


def write_reports(p, plan):
    fmt = lambda t: f"{int(t // 60):02d}:{t % 60:05.2f}"
    L = ["# Edit plan", "", f"Duration {fmt(plan['duration'])}", ""]
    if plan.get("hook_candidates"):
        L += ["## Hook candidates (strongest first)", ""]
        L += [f"- {fmt(h['start'])} — “{h['text']}”" for h in plan["hook_candidates"]]
        L.append("")
    L += ["## Sections", ""] + [f"- {fmt(s['start'])}–{fmt(s['end'])} **{s['mood']}** — {s['first_line']}"
                                for s in plan["sections"]]
    L += ["", "## Beats", "", "| # | Time | Layout | Visual | Text | Why |", "|---|---|---|---|---|---|"]
    for b in plan["beats"]:
        v = b.get("visual", {})
        vis = v.get("type", "")
        if vis == "map":
            vis += ": " + " → ".join(s["name"] for s in v["stops"])
        elif vis in ("year", "stat"):
            vis += f": {v.get('label') or v.get('value')} {v.get('unit', '')}"
        elif vis in ("image", "video"):
            vis += f": {v['file']} ({v['license']})"
        cam = b.get("camera", {})
        lay = b["layout"] + (f" ({cam.get('framing')}{', ' + cam['move'] if cam.get('move', 'none') != 'none' else ''})"
                             if b["layout"] == "presenter" else "")
        txt = " / ".join(t["text"] for t in b.get("text", []))
        L.append(f"| {b.get('id', '')} | {fmt(b['start'])}–{fmt(b['end'])} | {lay} | {vis} | {txt} | {b.get('reason', '')} |")
    L += ["", "## Music", ""] + [f"- {fmt(m['start'])}–{fmt(m['end'])}: {m['mood']}" for m in plan["music"]]
    L += ["", "## Sound effects", ""] + [f"- {fmt(s['t'])}: {s['kind']}" for s in plan["sfx"]]
    (p / "edit_report.md").write_text("\n".join(L) + "\n")

    S = ["# Shot list — visuals to source", "",
         "Gaps where the narration would be stronger with real imagery. Source in this order: your own footage, "
         "AI generation, public domain, CC0/CC-BY (commercial use allowed), licensed stock. Add each file to "
         "assets/ledger.json with its licence, then re-run `plan --force`.", ""]
    for r in plan["requests"]:
        S += [f"## {fmt(r['at'])}–{fmt(r['until'])}: {r['need']}", f"- Why: {r['why']}",
              f"- Narration: “{r['narration'][:240]}”", f"- Search terms: {r['suggest']['search']}",
              f"- AI prompt: {r['suggest']['ai_prompt']}",
              "- If AI-generated and depicting the past: add `\"ai_generated\": true, \"depicts\": \"historical\"` "
              "so it is labelled as a reconstruction.", ""]
    (p / "shot_list.md").write_text("\n".join(S) + "\n")


def cmd_render(args):
    p = _p(args)
    plan = read_json(p / "plan.json")
    fz = read_json(p / "faces.json")
    w, h = (1280, 720) if args.preview else (1920, 1080)
    comp = render.Compositor(p / "clean.mp4", plan, fz, w, h)
    log = comp.render(p / "video.mp4", preview=args.preview)
    render.save_log(log, p / "render_log.json")
    ledger = licenses.load_ledger(args.assets)
    music, fx = audio.build_beds(plan, licensed=ledger["usable"])
    audio.write_wav(p / "music.wav", music)
    audio.write_wav(p / "sfx.wav", fx)
    audio.mix(p / "clean.mp4", p / "music.wav", p / "sfx.wav", p / "mix.wav")
    out = p / ("preview.mp4" if args.preview else "final.mp4")
    render.mux(p / "video.mp4", p / "mix.wav", out)
    used_files = {b["visual"].get("file") for b in plan["beats"] if b.get("visual", {}).get("file")}
    used = [a for a in ledger["usable"] if a["file"] in used_files]
    write_json(p / "asset_log.json", {
        "external": [{k: a.get(k) for k in ("file", "license", "source", "author", "reference", "ai_generated")}
                     for a in used],
        "generated": [
            {"what": "maps", "source": "Natural Earth vector data", "license": "public-domain",
             "url": "https://www.naturalearthdata.com/about/terms-of-use/"},
            {"what": "music beds and sound effects", "source": "synthesised by docedit/audio.py", "license": "generated"},
            {"what": "fonts", "source": "Oswald, Source Sans 3 (Google Fonts)", "license": "SIL Open Font License 1.1"},
        ]})
    credits = licenses.credits(used)
    (p / "credits.txt").write_text("Credits (paste into the video description)\n\n" +
                                   ("\n".join(credits) if credits else "No attribution-required assets used.") +
                                   "\nMaps: Natural Earth (public domain).\n")
    print(f"rendered {out}")
    args.final = str(out)


def cmd_qc(args):
    p = _p(args)
    final = getattr(args, "final", None) or next((str(p / f) for f in ("final.mp4", "preview.mp4") if (p / f).exists()),
                                                 None)
    results, failed = qc.run_qc(p, licenses.load_ledger(args.assets), final)
    print((p / "qc_report.md").read_text())
    if failed:
        print(f"QC: {len(failed)} blocking issue(s)")
        return 1
    print("QC: all blocking checks passed")
    return 0


def cmd_run(args):
    cmd_analyze(args)
    cmd_clean(args)
    cmd_faces(args)
    args.force = True
    cmd_plan(args)
    if args.stop_after_plan:
        print("stopped after planning: review plan.json / edit_report.md, then `render` and `qc`")
        return 0
    cmd_render(args)
    return cmd_qc(args)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="docedit", description="Documentary editing pipeline for talking-head footage")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp, video=False):
        if video:
            sp.add_argument("video")
        sp.add_argument("-o", "--project", required=True, help="project folder for all artefacts")
        sp.add_argument("--assets", help="folder with ledger.json and licensed/AI/own media")
        sp.add_argument("--title", help="video title, shown once after the hook")
        sp.add_argument("--preview", action="store_true", help="fast 720p render")
        sp.add_argument("--denoise", choices=("auto", "strong", "off"), default="auto",
                        help="voice noise removal: auto (measured), strong (noisy rooms), off")
        return sp

    for name, fn, video in (("analyze", cmd_analyze, True), ("run", cmd_run, True)):
        sp = common(sub.add_parser(name), video)
        sp.add_argument("--transcript", help="existing transcript (.json words or .srt) instead of Whisper")
        sp.add_argument("--model", default="large-v3")
        sp.add_argument("--language", help="e.g. ur, en (auto-detect if omitted)")
        sp.set_defaults(fn=fn)
        if name == "run":
            sp.add_argument("--stop-after-plan", action="store_true")
            sp.add_argument("--cold-open", help="source seconds START-END to play first as the hook, e.g. 312.4-318.9")
    for name, fn in (("clean", cmd_clean), ("faces", cmd_faces), ("plan", cmd_plan), ("render", cmd_render),
                     ("qc", cmd_qc)):
        sp = common(sub.add_parser(name))
        sp.set_defaults(fn=fn)
        if name == "plan":
            sp.add_argument("--force", action="store_true", help="overwrite an existing plan.json")
        if name == "clean":
            sp.add_argument("--cold-open", help="source seconds START-END to play first as the hook, e.g. 312.4-318.9")
    args = ap.parse_args(argv)
    require_ffmpeg()
    sys.exit(args.fn(args) or 0)
