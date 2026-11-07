"""Pure: the reply, in fixed sections, from the checked envelope only. Re-render views cost no LLM call.
Markdown only: no raw HTML, no images, no links (data cells and findings are cleaned in checks.py)."""

from __future__ import annotations

import json
from collections.abc import Sequence

from checks import cell, clean_text
from fmt import fmt
from result import Result

EMPTY = "None found."
VIEWS = ("default", "chart", "findings", "caveats", "table")
_DTYPE_WORDS = {"numeric": "numeric", "datetime": "date", "category": "category", "boolean": "yes/no", "id": "id",
                "text": "text"}


def render(envelope: dict, results: Sequence[Result] = (), view: str = "default", source: str = "",
           extra_notes: Sequence[str] = ()) -> str:
    if view.startswith("table:"):
        body = [table_view(view.split(":", 1)[1], results)]
    elif view == "chart":
        body = [envelope.get("chart") or "**Chart**\n" + EMPTY]
    elif view == "findings":
        body = [_findings(envelope, source)]
    elif view == "caveats":
        body = [_caveats(envelope, source)]
    else:
        body = [header(envelope), _findings(envelope, source), envelope.get("chart") or "", _caveats(envelope, source)]
    return "\n\n".join(p for p in (*body, footer(envelope, view, extra_notes)) if p)


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False, default=str) + "\n```"


def header(envelope: dict) -> str:
    profile = envelope.get("profile") or {}
    counts = profile.get("counts") or {}
    kinds = ", ".join(f"{n} {_DTYPE_WORDS.get(k, k)}" for k, n in counts.items())
    return (f"**Table:** {fmt(profile.get('rows', 0))} rows × {fmt(profile.get('cols', 0))} columns ({kinds}). "
            "No code was run on your data beyond fixed analyses.")


def _findings(envelope: dict, source: str) -> str:
    lines = [f"{n}. {clean_text(f['text'], source)}" for n, f in enumerate(envelope.get("findings") or [], start=1)]
    return "**Key findings**\n" + ("\n".join(lines) or EMPTY)


def _caveats(envelope: dict, source: str) -> str:
    lines = [f"- {clean_text(c['text'], source)}" for c in envelope.get("caveats") or []]
    return "**Caveats**\n" + ("\n".join(lines) or EMPTY)


def table_view(rid: str, results: Sequence[Result]) -> str:
    result = next((r for r in results if r.id == rid), None)
    if result is None:
        known = ", ".join(r.id for r in results) or "none"
        return f"There is no analysis {cell(rid)} in this conversation (analyses: {known})."
    if not result.table:
        return f"**{result.id}: {cell(result.title)}**\n" + EMPTY
    head = "| " + " | ".join(cell(h) for h in result.headers) + " |"
    rule = "|" + "---|" * len(result.headers)
    body = ["| " + " | ".join(cell(c) for c in row) + " |" for row in result.table]
    return "\n".join([f"**{result.id}: {cell(result.title)}**", head, rule, *body])


def footer(envelope: dict, view: str, extra_notes: Sequence[str] = ()) -> str:
    ran = ", ".join(f"{a['id']} {a['kind']}" for a in envelope.get("analyses_run") or [])
    meta = [f"Analyses: {ran}." if ran and view == "default" else ""]
    skipped = envelope.get("analyses_skipped") or []
    if skipped:
        meta.append(f"{len(skipped)} skipped ({', '.join(sorted({s['reason'] for s in skipped}))}).")
    if envelope.get("dropped"):
        meta.append(f"{envelope['dropped']} model suggestions dropped by my checks.")
    if envelope.get("fell_back"):
        meta.append(f"{envelope['fell_back']} findings use the computed fact's own wording.")
    meta.append(f"Confidence: {envelope.get('confidence', 'low')}.")
    if envelope.get("needs_human"):
        meta.append("Worth a human look before you rely on it.")
    line = f"_({' '.join(m for m in meta if m)})_"
    notes = [n for n in (*(envelope.get("notes") or []), *extra_notes) if n]
    return "\n".join([line, *notes])
