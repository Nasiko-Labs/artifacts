"""Re-open the saved file and check it: slide count, every title present, notes present, 16:9, no empty
placeholder shapes, no slide text under the minimum size. Also the size and sha256 that bind an upload."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from pptx import Presentation
from pptx.util import Emu, Pt

import theme as t
from models import Deck


@dataclass(frozen=True)
class Verified:
    ok: bool
    problems: tuple[str, ...]
    size: int
    sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(data: bytes, deck: Deck) -> Verified:
    prs = Presentation(io.BytesIO(data))
    problems: list[str] = []
    slides = list(prs.slides)
    if len(slides) != len(deck.slides):
        problems.append(f"{len(slides)} slides in the file, {len(deck.slides)} in the deck")
    if abs(Emu(prs.slide_width).inches / Emu(prs.slide_height).inches - 16 / 9) > 0.01:
        problems.append("the slides are not 16:9")
    for n, (slide, spec) in enumerate(zip(slides, deck.slides, strict=False), start=1):
        problems.extend(f"slide {n}: {p}" for p in _slide_problems(slide, spec))
    return Verified(not problems, tuple(problems), len(data), sha256(data))


def _slide_problems(slide, spec) -> list[str]:
    problems = []
    text = "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)
    if spec.title and spec.title not in text:
        problems.append("title missing")
    if any(s.is_placeholder and s.has_text_frame and not s.text_frame.text.strip() for s in slide.shapes):
        problems.append("an empty placeholder")
    if spec.notes and not (slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip()):
        problems.append("speaker notes missing")
    small = [r for s in slide.shapes if s.has_text_frame for p in s.text_frame.paragraphs for r in p.runs
             if r.font.size is not None and r.font.size < Pt(t.MIN_SLIDE_PT)]
    if small:
        problems.append(f"text under {t.MIN_SLIDE_PT} pt")
    return problems
