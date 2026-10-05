import json

import pytest

from docedit.licenses import check_entry, load_ledger
from docedit.render import Compositor, _intersects, place_text


def test_text_never_placed_on_face():
    W, H = 1920, 1080
    face = [1100, 200, 500, 700]
    for prefer in ("left", "right"):
        box = place_text((120, 600, 4), W, H, [face], prefer)
        assert box and not _intersects(box, face)


def test_text_dropped_when_no_safe_space():
    W, H = 1920, 1080
    assert place_text((200, 900, 4), W, H, [[0, 0, W, H]], "left") is None


@pytest.mark.parametrize("entry,ok", [
    ({"file": "a.jpg", "license": "CC-BY-4.0", "source": "https://x", "author": "A"}, True),
    ({"file": "a.jpg", "license": "CC-BY-NC-4.0", "source": "https://x", "author": "A"}, False),
    ({"file": "a.jpg", "license": "CC-BY-4.0", "source": "https://x"}, False),          # no author
    ({"file": "a.jpg", "license": "", "source": "https://x"}, False),                    # unknown
    ({"file": "a.jpg", "license": "public-domain"}, False),                              # no source
    ({"file": "a.jpg", "license": "ai-generated"}, True),
    ({"file": "a.jpg", "license": "licensed-stock", "source": "https://x"}, False),      # no licence id
    ({"file": "a.jpg", "license": "own", "watermarked": True}, False),
])
def test_licence_policy(entry, ok):
    assert (not check_entry(entry)) == ok


def test_ledger_rejects_and_lists(tmp_path):
    (tmp_path / "good.jpg").write_bytes(b"x")
    (tmp_path / "bad.jpg").write_bytes(b"x")
    (tmp_path / "stray.png").write_bytes(b"x")
    (tmp_path / "ledger.json").write_text(json.dumps({"assets": [
        {"file": "good.jpg", "license": "CC0", "source": "https://x"},
        {"file": "bad.jpg", "license": "unknown"}]}))
    led = load_ledger(tmp_path)
    assert [a["file"] for a in led["usable"]] == ["good.jpg"]
    assert [a["file"] for a in led["rejected"]] == ["bad.jpg"]
    assert led["unlisted"] == ["stray.png"]
