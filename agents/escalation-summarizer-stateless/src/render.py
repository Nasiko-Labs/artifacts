"""Pure: the reply, rendered by code from the envelope only. Re-render views cost no LLM call."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

VIEWS = ("default", "sources", "ask", "impact", "json")
BULLETS_LEAD = "I couldn't write a paragraph I can back with the thread, so here are the checked facts:"


def render(env: Mapping[str, Any], view: str = "default", extra_notes: Sequence[str] = ()) -> str:
    if view == "json":
        return as_json(env)
    if view == "ask":
        return f"**Ask:** {env['ask']}"
    if view == "impact":
        return f"**Customer impact:** {env['customer_impact']}"
    if view == "sources":
        return sources(env)
    return "\n\n".join(part for part in (*_body(env), _footer(env, extra_notes)) if part)


def as_json(env: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(dict(env), ensure_ascii=False, indent=2) + "\n```"


def header(env: Mapping[str, Any]) -> str:
    timeline = env.get("timeline") or ""
    audience = f" for {env['audience']}" if env.get("audience") else ""
    return f"**Escalation summary{audience}**" + (f" ({timeline})" if timeline else "")


def sources(env: Mapping[str, Any]) -> str:
    lines = [f"**Sources** ({env.get('timeline') or 'the thread'})"]
    current = None
    for src in env.get("sources") or ():
        if src["sentence"] != current:
            current = src["sentence"]
            lines.append(f"- {current}")
        who = ", ".join(p for p in (src.get("speaker"), src.get("message")) if p)
        lines.append(f'  > "{_inline(src["snippet"])}"' + (f" ({who})" if who else ""))
    if len(lines) == 1:
        lines.append("No sentence-level sources: the summary is the checked facts list.")
    return "\n".join(lines)


def _body(env: Mapping[str, Any]) -> list[str]:
    summary = env.get("summary") or ""
    paragraph = f"{BULLETS_LEAD}\n{summary}" if env.get("summary_format") == "bullets" else summary
    return [header(env), paragraph, f"**Ask:** {env['ask']}", f"**Customer impact:** {env['customer_impact']}",
            *(env.get("conflicts") or ())]


def _footer(env: Mapping[str, Any], extra_notes: Sequence[str]) -> str:
    lines = []
    left_out = int(env.get("left_out") or 0)
    if left_out:
        unit = "statement" if left_out == 1 else "statements"
        lines.append(f"_({left_out} {unit} left out: no support in the thread.)_")
    if env.get("needs_human"):
        lines.append("_Needs a human check: the ask or the impact isn't stated, or the thread is unclear._")
    return "\n".join([*lines, *(env.get("notes") or ()), *extra_notes])


def _inline(text: str) -> str:
    return " ".join(str(text).split()).replace("<", "‹").replace(">", "›")
