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
