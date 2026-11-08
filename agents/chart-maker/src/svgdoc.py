"""Pure: the SVG wrapper and the html page around it. Text is XML-escaped; the page carries no script,
no image, no link and no external font, and handles dark mode with currentColor."""

from __future__ import annotations

from xml.sax.saxutils import escape

from palette import SERIES_COLOURS

WIDTH = 800
_ATTR = {'"': "&quot;", "'": "&apos;"}
_STYLE = ("text{fill:currentColor}.grid{stroke:currentColor;stroke-opacity:.15}"
          ".axis{stroke:currentColor;stroke-opacity:.6}.muted{fill-opacity:.7}"
          + "".join(f".s{i}{{fill:{c};stroke:{c}}}" for i, c in enumerate(SERIES_COLOURS))
          + ".line{fill:none;stroke-width:2}")
_PAGE_STYLE = ("body{margin:24px;font-family:system-ui,sans-serif;background:#ffffff;color:#1a1a1a}"
               "@media (prefers-color-scheme: dark){body{background:#121212;color:#ececec}}")


def text(value: str) -> str:
    return escape(value)


def attr(value: str) -> str:
    return escape(value, _ATTR)


def svg(height: int, heading: str, title: str, desc: str, body: list[str]) -> str:
    """`heading` is the visible title; `title` and `desc` are the accessible name and description."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" '
            f'height="{height}" role="img" font-family="system-ui, sans-serif" font-size="12">'
            f"<title>{text(title)}</title><desc>{text(desc)}</desc><style>{_STYLE}</style>"
            f'<text x="16" y="24" font-size="16" font-weight="600">{text(heading)}</text>'
            + "".join(body) + "</svg>")


def page(title: str, svg_markup: str) -> str:
    return (f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"/><title>{text(title)}</title>'
            f"<style>{_PAGE_STYLE}</style></head><body>{svg_markup}</body></html>")


def label(x: float, y: float, value: str, anchor: str = "start", extra: str = "") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}"{extra}>{text(value)}</text>'
