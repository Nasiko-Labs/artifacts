"""Pure: the reply text (markdown for people, or the envelope as a fenced json block when asked) and the
envelope itself. Everything here is code-rendered; no raw HTML outside the html code block."""

from __future__ import annotations

import json
from dataclasses import dataclass

import prompts
from chartflow import TYPE_NAMES, ChartOut
from diagramflow import KIND_NAMES, DiagramOut
from fmt import code_block, md


@dataclass(frozen=True)
class Reply:
    text: str
    envelope: dict | None = None
    file: tuple[str, str, bytes] | None = None   # (name, media type, bytes) for API clients


def chart_reply(out: ChartOut, as_json: bool) -> Reply:
    envelope = {"summary": out.summary, "items": list(out.items), "confidence": out.confidence,
                "needs_human": out.needs_human, "mode": "chart", "chart_type": out.chart_type,
                "text_chart": out.text_chart, "table_md": out.table_md, "svg": out.svg, "mermaid": out.mermaid,
                "outline": None, "notes": list(out.notes)}
    file = ("chart.svg", "image/svg+xml", out.svg.encode("utf-8"))
    if as_json:
        return Reply(_json_block(envelope), envelope, file)
    caption = out.summary.split(": ", 1)[1] if ": " in out.summary else ""
    parts = [f"**{TYPE_NAMES[out.chart_type]}: {md(out.title)}** ({md(caption).replace(', ', ' · ')})",
             code_block("text", out.text_chart), out.table_md, code_block("html", out.html)]
    if out.mermaid:
        parts.append(code_block("mermaid", out.mermaid))
    parts += [_notes(out.notes), prompts.FOOTER_CHART]
    return Reply("\n\n".join(p for p in parts if p), envelope, file)


def diagram_reply(out: DiagramOut, kind: str, as_json: bool) -> Reply:
    envelope = {"summary": out.summary, "items": list(out.items), "confidence": out.confidence,
                "needs_human": out.needs_human, "mode": "diagram", "chart_type": None, "text_chart": None,
                "table_md": None, "svg": None, "mermaid": out.mermaid, "outline": out.outline,
                "notes": list(out.notes)}
    file = ("diagram.mmd", "text/plain", out.mermaid.encode("utf-8"))
    if as_json:
        return Reply(_json_block(envelope), envelope, file)
    caption = out.summary.split(" (", 1)[1].rstrip(")") if " (" in out.summary else ""
    parts = [f"**{KIND_NAMES[kind]}: {md(out.title)}** ({md(caption)})", out.outline,
             code_block("mermaid", out.mermaid), _notes(out.notes), prompts.FOOTER_DIAGRAM]
    return Reply("\n\n".join(p for p in parts if p), envelope, file)


def csv_reply(csv_text: str) -> Reply:
    return Reply("The data as CSV (Download saves it as a .csv file):\n\n" + code_block("csv", csv_text))


def message(text: str, as_json: bool = False, needs_human: bool = True) -> Reply:
    """A plain answer (refusal, limit, question). In json mode it is still an envelope."""
    if not as_json:
        return Reply(text)
    envelope = {"summary": text, "items": [], "confidence": "low", "needs_human": needs_human, "mode": None,
                "chart_type": None, "text_chart": None, "table_md": None, "svg": None, "mermaid": None,
                "outline": None, "notes": []}
    return Reply(_json_block(envelope), envelope)


def _notes(notes: tuple[str, ...]) -> str:
    return "\n".join(f"- _{md(n)}_" for n in notes)


def _json_block(envelope: dict) -> str:
    return code_block("json", json.dumps(envelope, ensure_ascii=False, indent=2))
