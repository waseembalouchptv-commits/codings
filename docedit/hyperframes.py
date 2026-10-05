"""Export a docedit project as an editable HyperFrames composition.

docedit owns the parts that need pixel analysis: cuts, face tracking, face-safe
layouts, maps and graphics. HyperFrames (HTML -> MP4, https://github.com/heygen-com/hyperframes)
is the editing surface afterwards. The export splits the cut into layers:

  assets/picture.mp4   the directed picture (presenter, maps, graphics), no captions
  index.html           every caption as an editable, GSAP-animated HTML element,
                       placed where docedit put it (clear of the face)
  assets/voice.wav     cleaned narration
  assets/music.wav     generated score, already ducked under the voice
  assets/sfx.wav       sound effects

Retime, rewrite or restyle captions, rebalance audio, or add HyperFrames registry
blocks, then `npx hyperframes preview` / `npx hyperframes render`.

When moving or adding text, keep it out of each caption's `data-face-zone` (output
pixels x,y,w,h): that is the presenter's protected face area during that clip.
"""
import html
import json
import shutil
from pathlib import Path

from . import audio
from .media import read_json, run, write_json
from .style import FONTS, STYLE

STYLES = {  # mirrors render._text_block, as a fraction of frame height
    "title": (0.085, 700, 3), "label": (0.045, 600, 4), "callout": (0.11, 700, 2),
}


def _rgb(c):
    return "#%02x%02x%02x" % c


def export(project: Path, out: Path = None, width=1920, height=1080):
    from .render import Compositor
    project = Path(project)
    out = Path(out or project / "hyperframes")
    a = out / "assets"
    (a / "fonts").mkdir(parents=True, exist_ok=True)
    plan = read_json(project / "plan.json")
    faces = read_json(project / "faces.json")

    # picture without captions
    comp = Compositor(project / "clean.mp4", plan, faces, width, height, draw_text=False)
    log = comp.render(a / "picture.mp4")
    write_json(out / "docedit_layout.json", log)

    # audio stems: processed voice, ducked music, sfx
    music, fx = audio.build_beds(plan)
    audio.write_wav(project / "music.wav", music)
    audio.write_wav(a / "sfx.wav", fx)
    voice_fx = ("equalizer=f=250:t=q:w=1.2:g=-2,equalizer=f=3500:t=q:w=1.0:g=2,"
                "acompressor=threshold=-20dB:ratio=3:attack=8:release=120:makeup=2,"
                f"aresample={audio.SR},aformat=channel_layouts=stereo")
    run(["ffmpeg", "-y", "-v", "error", "-i", project / "clean.mp4", "-vn", "-af",
         voice_fx + ",loudnorm=I=-15:TP=-1.5:LRA=11", "-ar", audio.SR, a / "voice.wav"])
    run(["ffmpeg", "-y", "-v", "error", "-i", project / "music.wav", "-i", a / "voice.wav", "-filter_complex",
         "[0:a][1:a]sidechaincompress=threshold=0.03:ratio=8:attack=40:release=500[m]", "-map", "[m]",
         "-ar", audio.SR, a / "music.wav"])
    gsap = Path(__file__).resolve().parent.parent / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    local_gsap = gsap.exists()
    if local_gsap:  # offline-safe: renders never depend on a CDN (run `npm install` at the repo root)
        shutil.copy(gsap, a / "gsap.min.js")
    for f in ("Oswald.ttf", "SourceSans3.ttf", "OFL-Oswald.txt", "OFL-SourceSans3.txt"):
        shutil.copy(FONTS / f, a / "fonts" / f)

    captions = []
    for rec in log:
        for el in rec["elements"]:
            if el["kind"] == "text":
                captions.append((el, rec.get("face_zone")))
    (out / "index.html").write_text(_html(plan, captions, width, height, local_gsap))
    (out / "README.md").write_text(_readme(plan, len(captions)))
    write_json(out / "hyperframes.json", {"name": project.name, "entry": "index.html"}) \
        if not (out / "hyperframes.json").exists() else None
    return out


def _html(plan, captions, W, H, local_gsap=True):
    dur = plan["duration"]
    cap_html, tweens = [], []
    for i, (el, fz) in enumerate(captions):
        size_k, weight, track = STYLES.get(el.get("style", "callout"), STYLES["callout"])
        size = round(H * size_k * el.get("scale", 1.0))
        pad = round(size * 0.35)
        x, y = el["box"][0] + pad, el["box"][1] + pad - size * 0.15  # 1.3 line-height leading
        start, end = el["start"], el["end"]
        cid = f"cap{i:03d}"
        bar = "label" if el.get("style") == "label" else "under"
        face = ",".join(str(round(v)) for v in fz) if fz else ""
        cap_html.append(
            f'      <div id="{cid}" class="clip caption {bar}" data-start="{start:.3f}" '
            f'data-duration="{end - start:.3f}" data-track-index="1" data-face-zone="{face}" '
            f'style="left:{x:.0f}px;top:{y:.0f}px;font-size:{size}px;font-weight:{weight};'
            f'letter-spacing:{track}px">\n'
            f'        <span id="{cid}-t" class="cap-text">{html.escape(el["text"])}</span>\n'
            f'      </div>')
        tweens.append(f'      tl.fromTo("#{cid}-t", {{ opacity: 0, y: {round(H * 0.015)} }}, '
                      f'{{ opacity: 1, y: 0, duration: {STYLE.enter}, ease: "power2.out" }}, {start:.3f});')
        tweens.append(f'      tl.to("#{cid}-t", {{ opacity: 0, duration: {STYLE.exit}, ease: "power1.in" }}, '
                      f'{max(start, end - STYLE.exit):.3f});')
    paper, accent = _rgb(STYLE.paper), _rgb(STYLE.accent)
    gsap_src = "assets/gsap.min.js" if local_gsap else "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>{html.escape(plan.get("title") or "docedit export")}</title>
    <script src="{gsap_src}"></script>
    <style>
      @font-face {{ font-family: "Oswald"; src: url("assets/fonts/Oswald.ttf"); font-weight: 200 700; }}
      @font-face {{ font-family: "Source Sans 3"; src: url("assets/fonts/SourceSans3.ttf"); font-weight: 200 900; }}
      body {{ margin: 0; background: #000; }}
      #root {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
      #picture {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
      /* Captions: English, editorial, never over the face (see data-face-zone on each). */
      .caption {{ position: absolute; font-family: "Oswald", sans-serif; color: {paper}; line-height: 1.3;
                  white-space: nowrap; }}
      .cap-text {{ display: block; text-shadow: 0 2px 14px rgba(0, 0, 0, 0.65); }}
      .caption.under .cap-text {{ padding-bottom: 0.28em; background: linear-gradient({accent}, {accent})
                  left bottom / 35% 0.09em no-repeat; }}
      .caption.label .cap-text {{ padding-left: 0.42em; border-left: 0.12em solid {accent}; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-width="{W}" data-height="{H}"
         data-duration="{dur:.3f}">
      <video id="picture" class="clip" src="assets/picture.mp4" muted playsinline
             data-start="0" data-duration="{dur:.3f}" data-track-index="0"></video>
{chr(10).join(cap_html)}
      <audio id="voice" src="assets/voice.wav" data-start="0" data-duration="{dur:.3f}"
             data-track-index="2" data-volume="1"></audio>
      <audio id="music" src="assets/music.wav" data-start="0" data-duration="{dur:.3f}"
             data-track-index="3" data-volume="1"></audio>
      <audio id="sfx" src="assets/sfx.wav" data-start="0" data-duration="{dur:.3f}"
             data-track-index="4" data-volume="1"></audio>
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join(tweens)}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def _readme(plan, n):
    return f"""# HyperFrames edit: {plan.get("title") or "untitled"}

Exported by `python -m docedit export-hf`. Layers:

| Track | Element | What it is |
|---|---|---|
| 0 | `#picture` | Directed picture from docedit (presenter, maps, graphics), **no captions** |
| 1 | `.caption` ({n}) | Editable English captions, GSAP fade/slide |
| 2 | `#voice` | Cleaned narration |
| 3 | `#music` | Generated score, ducked under the voice |
| 4 | `#sfx` | Sound effects |

```bash
npx hyperframes check     # lint + layout + contrast
npx hyperframes preview   # Studio in the browser: drag, retime, edit text
npx hyperframes render    # MP4
```

Rules from the channel brief still apply. Each caption carries `data-face-zone`
(x,y,w,h in pixels): the presenter's face for that moment. Keep text out of it,
keep captions to 4 words or fewer, and write them in English.

To change cuts, maps or layouts, edit the docedit project (`plan.json`) and
export again. Re-exporting overwrites `index.html`.
"""
