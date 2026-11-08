"""Pure: the markdown reply, rendered in code. No raw HTML, no images, no links; every value shown is taken
out of the user's paste or a tool result, inside code spans with backticks removed."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import drift as dr
import prompts
from envelope import plural

MAX_SHOWN = 25
MAX_VALUE_CHARS = 140
_FLAG_TEXT = {
    "iac_differs_from_state": "the IaC value also differs from the last known state",
    "sensitive": "value hidden (sensitive)",
    "unresolved": "the IaC uses a variable here, so `expected` is the last known state",
}


def value(v: Any) -> str:
    text = "null" if v is None else v if isinstance(v, str) else json.dumps(v, default=str)
    text = " ".join(text.replace("`", "'").split())
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "..."


def code(v: Any) -> str:
    return f"`{value(v)}`"


def title(drifts: Sequence[dr.Drift], repo: str, unresolved: int) -> str:
    where = f" · {value(repo)}" if repo else ""
    body = (f"**Config drift{where} · {plural(len(drifts), 'drifted attribute')} in "
            f"{plural(len(dr.resources(drifts)), 'resource')}**")
    return body + (f" ({plural(unresolved, 'unresolved attribute')} skipped)" if unresolved else "")


def change_text(d: dr.Drift) -> str:
    if d.kind == "missing_live" and not d.path:
        return "resource deleted live"
    if d.kind == "missing_live":
        return f"set in code to {code(d.expected)}, missing live"
    if d.kind == "extra_live":
        return f"live has {code(d.actual)}, not set in code"
    return f"expected {code(d.expected)}, actual {code(d.actual)}"


def who(changed_by: Any) -> str:
    if isinstance(changed_by, dict):
        tag = " (automation)" if changed_by.get("automation") else ""
        return (f"changed by: {code(changed_by.get('actor'))}{tag} via {value(changed_by.get('event'))} on "
                f"{value(changed_by.get('time'))}")
    return f"changed by: {value(changed_by)}"


def item(n: int, d: dr.Drift, detail: bool = False) -> str:
    head = f"{n}. **{d.severity.upper()}** {code(d.resource)} {code(d.attribute)}: {change_text(d)}"
    where = code(d.location) if d.file else "IaC file: unknown"
    author = (f"last changed in code by {value(d.last_code_author)} ({value(d.last_code_change)})"
              if d.last_code_author != dr.UNKNOWN else "last code author: unknown")
    lines = [head, f"   - {where} · {author} · {who(d.changed_by)}"]
    lines += [f"   - Note: {_FLAG_TEXT[f]}" for f in d.flags if f in _FLAG_TEXT]
    if detail:
        lines += [f"   - Expected (code): {code(d.expected)}", f"   - Actual (live): {code(d.actual)}"]
    if d.why:
        lines.append(f"   - Why it matters: {d.why}")
    return "\n".join(lines)


def report(drifts: Sequence[dr.Drift], *, repo: str, unresolved: int, compared: int, headline: str,
           notes: Sequence[str], extras: Sequence[dr.Drift] = (), detail: bool = False) -> str:
    if not drifts and not extras:
        body = prompts.NO_DRIFT.format(compared=plural(compared, "attribute"), skipped=plural(unresolved, "attribute"))
        return "\n\n".join([body, *_coverage(notes), prompts.READ_ONLY])
    parts = [title(drifts, repo, unresolved)]
    if headline:
        parts.append(headline)
    shown = list(drifts[:MAX_SHOWN])
    if shown:
        parts.append("\n".join(item(i, d, detail) for i, d in enumerate(shown, 1)))
    if len(drifts) > MAX_SHOWN:
        parts.append(f"...and {len(drifts) - MAX_SHOWN} more (all are in the data part).")
    if extras:
        parts.append("**Set live, not in code** (low)\n" + "\n".join(
            f"- {code(d.resource)} {code(d.attribute)}: {code(d.actual)}" for d in extras[:MAX_SHOWN]))
    return "\n\n".join([*parts, *_coverage(notes), prompts.READ_ONLY])


def _coverage(notes: Sequence[str]) -> list[str]:
    return ["**Coverage**\n" + "\n".join(f"- {n}" for n in notes)] if notes else []


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, default=str) + "\n```"
