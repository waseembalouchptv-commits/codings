"""The documentary-edit skill bundles copies of docs so it also works outside the repo; keep them in sync."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "documentary-edit"


@pytest.mark.parametrize("doc,ref", [("DIRECTING_BRIEF.md", "directing-brief.md"), ("PLAN_FORMAT.md", "plan-format.md")])
def test_skill_references_match_docs(doc, ref):
    assert (SKILL / "references" / ref).read_text() == (ROOT / "docs" / doc).read_text(), \
        f"copy docs/{doc} to .claude/skills/documentary-edit/references/{ref}"


def test_skill_commands_exist():
    from docedit.cli import main
    text = (SKILL / "SKILL.md").read_text()
    for cmd in ("run", "clean", "render", "qc", "export-hf"):
        assert f"python -m docedit {cmd}" in text
        with pytest.raises(SystemExit) as e:
            main([cmd, "--help"])
        assert e.value.code == 0
