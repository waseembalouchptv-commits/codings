"""Channel visual identity: one place for colours, type and motion timing.

The look is deliberately original: warm sandstone accent (Balochistan landscape),
deep ink-blue backgrounds, condensed display type for kinetic words and a humanist
sans for labels. Edit this file to restyle every generated graphic at once.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

DATA = Path(__file__).resolve().parent / "data"
FONTS = DATA / "fonts"


@dataclass(frozen=True)
class Style:
    # colours (RGB)
    ink: tuple = (14, 20, 30)            # graphic backgrounds
    ink_2: tuple = (24, 33, 47)          # panels, land outside focus
    land: tuple = (44, 55, 70)           # neighbouring countries
    land_focus: tuple = (72, 84, 98)     # focus country
    border: tuple = (120, 132, 146)
    water: tuple = (10, 15, 23)
    river: tuple = (86, 150, 196)
    paper: tuple = (240, 234, 222)       # primary text
    muted: tuple = (164, 170, 178)       # secondary text
    accent: tuple = (214, 150, 74)       # sandstone: highlights, pins, rules
    accent_2: tuple = (176, 72, 52)      # rare second accent (conflict, disaster)

    display_font: str = "Oswald.ttf"
    text_font: str = "SourceSans3.ttf"

    # motion
    enter: float = 0.45                  # seconds for elements to animate in
    exit: float = 0.30
    min_text_hold: float = 1.6           # never show text shorter than this

    # layout
    margin: float = 0.06                 # safe margin as fraction of frame width
    face_pad: float = 0.35               # protected zone = face box grown by this fraction
    max_caption_words: int = 4

    # grade (kept gentle: natural skin tones)
    grade: dict = field(default_factory=lambda: {"contrast": 1.04, "saturation": 1.03, "gamma": 1.0,
                                                  "max_wb_gain": 0.08})


STYLE = Style()


@lru_cache(maxsize=64)
def font(kind: str, size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    name = STYLE.display_font if kind == "display" else STYLE.text_font
    try:
        f = ImageFont.truetype(str(FONTS / name), size)
        try:
            f.set_variation_by_name(weight)
        except (OSError, ValueError):
            pass
        return f
    except OSError:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
