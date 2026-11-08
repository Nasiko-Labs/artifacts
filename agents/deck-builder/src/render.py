"""Pure: the markdown reply (outline, notes, fallback pointers) and the envelope DataPart, all in code."""

from __future__ import annotations

import json
from collections import Counter

from models import Deck, Slide
from structure import Report

MAX_PLACEHOLDER_SLIDES = 6
_KIND = {"title": "title", "closing": "closing", "chart": "chart", "table": "table", "big_number": "big number",
         "quote": "quote", "two_column": "two columns"}


def file_name(deck: Deck, override: str | None = None) -> str:
    base = (override or deck.title or "deck").strip()
    base = "".join(ch for ch in base if ch.isalnum() or ch in " -_.()&").strip(" .") or "deck"
    return base[:80] + ("" if base.casefold().endswith(".pptx") else ".pptx")


def size_text(size: int) -> str:
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def _slide_line(n: int, slide: Slide) -> str:
    kind = _KIND.get(slide.layout)
    detail = f"{kind}" if kind else ""
    if slide.layout == "chart" and slide.chart:
        detail = f"chart: {', '.join(slide.chart.value_columns)} by {slide.chart.label_column}"
    bullets = len(slide.bullets) + len(slide.right)
    tail = f" — {bullets} bullet{'s' if bullets != 1 else ''}" if bullets and slide.layout != "title" else ""
    return f"{n}. **{slide.title}**" + (f" ({detail})" if detail else "") + tail


def placeholder_summary(report: Report) -> str:
    if not report.placeholders:
        return "no placeholders"
    slides = Counter(p.split(":")[0] for p in report.placeholders)
    marks = sorted({p.split(": ")[1] for p in report.placeholders})
    where = ", ".join(s.replace("slide ", "") for s in list(slides)[:MAX_PLACEHOLDER_SLIDES])
    count = len(report.placeholders)
    return f"{count} placeholder{'s' if count != 1 else ''} to fill ({' '.join(f'`{m}`' for m in marks)} on " \
           f"slide{'s' if len(slides) != 1 else ''} {where})"


def outline(deck: Deck, report: Report, lead: str = "Deck built") -> str:
    head = f"**{lead}: \"{deck.title}\"** · {len(deck.slides)} slides · {placeholder_summary(report)}"
    lines = [head, *(_slide_line(n, s) for n, s in enumerate(deck.slides, start=1))]
    lines.append("_Speaker notes are on every slide. Say \"make slide 3 a chart of my table\", \"shorter\", or "
                 "\"upload to Drive\"._")
    return "\n".join(lines)


def notes_block(notes: list[str]) -> str:
    return "\n".join(f"- {n}" for n in notes)


def delivery_hint(upload_offered: bool) -> str:
    if upload_offered:
        return "Say **script** for a file-free way to rebuild it."
    return ("Chat can't download files, so: say **upload** to put it in your Drive (you'll approve first), or "
            "**script** for a file-free way to rebuild it.")


def script_block(script: str) -> str:
    return "Save this as `build_deck.py`, then run `pip install python-pptx && python build_deck.py`:\n\n" \
           f"```python\n{script}```"


def json_block(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=1, ensure_ascii=False) + "\n```"


def confidence(report: Report, chart_failures: int) -> str:
    count = len(report.placeholders)
    if count > 3 or report.dropped or chart_failures:
        return "low"
    if count or report.capped or report.repaired:
        return "medium"
    return "high"


def envelope(deck: Deck, report: Report, *, name: str, size: int, sha: str, chart_failures: int,
             uploaded: dict | None, upload_unavailable: bool, script: str | None = None) -> dict:
    level = confidence(report, chart_failures)
    count = len(report.placeholders)
    summary = (f"{len(deck.slides)}-slide deck '{deck.title}' built; "
               + (f"{count} placeholder{'s' if count != 1 else ''} to fill." if count else "no placeholders."))
    items = [{"n": n, "title": s.title, "layout": s.layout, "body": "\n".join([*s.bullets, *s.right])}
             for n, s in enumerate(deck.slides, start=1)]
    return {"summary": summary, "items": items, "confidence": level,
            "needs_human": level == "low" or bool(count) or upload_unavailable,
            "slide_count": len(deck.slides), "placeholders": list(report.placeholders), "file_name": name,
            "file_bytes": size, "sha256": sha, "uploaded": uploaded, "deck_json": deck.dump(), "script": script}
