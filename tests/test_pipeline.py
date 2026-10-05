"""End-to-end: synthetic footage -> final video -> QC passes."""
import json
import shutil

import pytest

from docedit.cli import main
from tests.fixture import make

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def test_full_pipeline(tmp_path):
    raw, tr = make(tmp_path / "src", w=640, h=360)
    proj = tmp_path / "proj"
    with pytest.raises(SystemExit) as e:
        main(["run", str(raw), "-o", str(proj), "--transcript", str(tr), "--title", "Quetta", "--preview"])
    assert e.value.code == 0, (proj / "qc_report.md").read_text()
    for f in ("cleanup.json", "plan.json", "edit_report.md", "shot_list.md", "render_log.json", "preview.mp4",
              "credits.txt", "asset_log.json", "qc_report.md"):
        assert (proj / f).exists(), f
    cl = json.loads((proj / "cleanup.json").read_text())
    whys = {r["why"] for r in cl["removed_words"]}
    assert {"filler", "retake", "stutter"} <= whys
    assert cl["clean_duration"] < cl["source_duration"] - 5


def test_export_to_hyperframes(tmp_path):
    import subprocess
    from pathlib import Path
    raw, tr = make(tmp_path / "src", w=640, h=360)
    proj = tmp_path / "proj"
    with pytest.raises(SystemExit):
        main(["run", str(raw), "-o", str(proj), "--transcript", str(tr), "--title", "Quetta", "--stop-after-plan"])
    with pytest.raises(SystemExit) as e:
        main(["export-hf", "-o", str(proj)])
    assert e.value.code == 0
    hf = proj / "hyperframes"
    page = (hf / "index.html").read_text()
    for f in ("picture.mp4", "voice.wav", "music.wav", "sfx.wav", "fonts/Oswald.ttf"):
        assert (hf / "assets" / f).exists(), f
    assert 'data-composition-id="main"' in page
    # captions in the HTML are exactly docedit's face-safe placements (none dropped or invented)
    layout = json.loads((hf / "docedit_layout.json").read_text())
    assert sum(el["kind"] == "text" for r in layout for el in r["elements"]) == page.count('class="clip caption')
    cli = Path(__file__).resolve().parent.parent / "node_modules" / ".bin" / "hyperframes"
    if cli.exists():
        out = subprocess.run([str(cli), "lint"], cwd=hf, capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr


def test_caption_html_carries_face_zone_and_position():
    from docedit.hyperframes import _html
    el = {"text": "QUETTA", "box": [100, 600, 300, 120], "start": 2.0, "end": 5.0, "style": "label", "scale": 1.0}
    page = _html({"duration": 10.0, "title": "T"}, [(el, [900, 100, 500, 700])], 1920, 1080)
    assert 'data-face-zone="900,100,500,700"' in page
    assert 'data-start="2.000"' in page and 'data-duration="3.000"' in page and ">QUETTA<" in page
    assert 'window.__timelines["main"]' in page
