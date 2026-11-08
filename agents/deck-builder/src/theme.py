"""Pure: the one built-in theme: fonts, sizes, geometry (inches on a 13.333 x 7.5 canvas), the default
colour-blind-safe palette, user hex colours checked by the WCAG contrast ratio, and the text-fit heuristic."""

from __future__ import annotations

import re
from dataclasses import dataclass

from models import Theme

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
FONT = "Calibri"
TITLE_PT = 32
BODY_PT = 20
MIN_SLIDE_PT = 18
NOTES_PT = 12
BIG_NUMBER_PT = 72
TEXT_COLOUR = "1F2937"
BACKGROUND = "FFFFFF"
DEFAULT_PALETTE = ("0072B2", "E69F00", "009E73")   # Okabe-Ito: colour-blind safe
MIN_CONTRAST = 4.5
MAX_USER_COLOURS = 3

MARGIN_IN = 0.6
TITLE_TOP_IN = 0.4
TITLE_H_IN = 1.2
BODY_TOP_IN = 1.8
BODY_H_IN = 5.2
_HEX = re.compile(r"(?<![0-9A-Za-z])#([0-9A-Fa-f]{6})\b")


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    width: float
    height: float


TITLE_BOX = Box(MARGIN_IN, TITLE_TOP_IN, SLIDE_W_IN - 2 * MARGIN_IN, TITLE_H_IN)
BODY_BOX = Box(MARGIN_IN, BODY_TOP_IN, SLIDE_W_IN - 2 * MARGIN_IN, BODY_H_IN)
LEFT_BOX = Box(MARGIN_IN, BODY_TOP_IN, (SLIDE_W_IN - 3 * MARGIN_IN) / 2, BODY_H_IN)
RIGHT_BOX = Box(MARGIN_IN * 2 + LEFT_BOX.width, BODY_TOP_IN, LEFT_BOX.width, BODY_H_IN)


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str = BACKGROUND) -> float:
    """WCAG 2 contrast ratio, 1.0-21.0."""
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def theme_from_text(text: str) -> tuple[Theme, tuple[str, ...]]:
    """The theme with the user's typed hex colours (up to 3) that read on white; a note per rejected one."""
    colours = list(DEFAULT_PALETTE)
    notes: list[str] = []
    accepted = 0
    for raw in _HEX.findall(text)[:MAX_USER_COLOURS]:
        colour = raw.upper()
        ratio = contrast_ratio(colour)
        if ratio < MIN_CONTRAST:
            notes.append(f"Colour #{colour} was not used: its contrast on white is {ratio:.1f}:1, below "
                         f"{MIN_CONTRAST}:1.")
            continue
        colours[accepted] = colour
        accepted += 1
    return Theme(accent=colours[0], second=colours[1], third=colours[2]), tuple(notes)


def line_capacity(box: Box, pt: int) -> int:
    """Characters that fit on one line (Calibri averages about half an em per character)."""
    return max(1, int(box.width * 72 / (pt * 0.5)))


def lines_capacity(box: Box, pt: int) -> int:
    return max(1, int(box.height * 72 / (pt * 1.2)))


def fits(texts: list[str], box: Box, pt: int = BODY_PT) -> bool:
    """Estimated: the wrapped lines of `texts` fit in the box at this size (no layout engine)."""
    per_line = line_capacity(box, pt)
    needed = sum(max(1, -(-len(t) // per_line)) for t in texts) + max(0, len(texts) - 1) * 0.3
    return needed <= lines_capacity(box, pt)
