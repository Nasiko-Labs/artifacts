"""Pure: reading the crew's answers (locator, critic, skeptical review lead) into checked data.

The critic's flags go through `requirements.parse_flags` (unknown ids ignored, unverifiable terms nulled);
in the unheaded case its ids are `L<n>` line numbers, mapped here to the requirement on that line. The review
lead can only take points away: a flag it calls unsupported is reset to its no-deduction value, a question
it drops is removed. An id or flag name it made up is ignored."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from requirements import Flags, Requirement, parse_flags
from sections import LABELS, REQUIRED

OFF_TOPIC = "off_topic"
NO_DEDUCTION = {"single_behavior": True, "has_actor": True, "testable": True, "has_condition_or_limit": True,
                "contains_solution_detail": False, "ambiguous_term": None}
_QID = re.compile(r"^q\d{1,2}$")
_LINE_ID = re.compile(r"^L(\d{1,5})$", re.IGNORECASE)


def located(data: Any, missing: Sequence[str]) -> dict[str, list[tuple[int, int]]] | str | None:
    """Ranges by section key, "off_topic", or None when the answer does not match the contract."""
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return OFF_TOPIC
    found = data.get("sections")
    if not isinstance(found, dict):
        return None
    by_label = {LABELS[k].casefold(): k for k in REQUIRED}
    ranges: dict[str, list[tuple[int, int]]] = {}
    for name, spans in found.items():
        key = by_label.get(str(name).casefold()) or (name if name in LABELS else None)
        if key in missing and isinstance(spans, list):
            ranges[key] = [(int(s[0]), int(s[1])) for s in spans
                           if isinstance(s, list | tuple) and len(s) == 2 and all(isinstance(v, int) for v in s)]
    return ranges


def critic(data: Any) -> dict | str | None:
    """{"requirements": [...], "questions": [...]} with question ids, "off_topic", or None."""
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return OFF_TOPIC
    rows, asked = data.get("requirements") or [], data.get("questions") or []
    if data.get("status") not in ("ok", None) or not isinstance(rows, list) or not isinstance(asked, list):
        return None
    asked = [q for q in asked if isinstance(q, dict)]
    ids = [str(q.get("id") or "").strip().casefold() for q in asked]
    usable = all(_QID.match(i) for i in ids) and len(set(ids)) == len(ids)
    questions = [{**q, "id": ids[n] if usable else f"q{n + 1}"} for n, q in enumerate(asked)]
    return {"requirements": rows, "questions": questions}


def flags_for(rows: list, reqs: Sequence[Requirement]) -> dict[str, Flags]:
    """Validated flags by requirement id; `L<n>` ids name the requirement on line n."""
    by_line = {r.line: r.id for r in reqs}
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        match = _LINE_ID.match(str(row.get("id") or "").strip())
        mapped.append({**row, "id": by_line.get(int(match.group(1)), "") if match else row.get("id")})
    return parse_flags({"requirements": mapped}, reqs)


def review(data: Any) -> dict | None:
    """The review lead's two lists, or None when its answer is unusable (or it declined)."""
    if not isinstance(data, dict) or data.get("status") == OFF_TOPIC:
        return None
    unsupported, drops = data.get("unsupported_flags") or [], data.get("drop_questions") or []
    if not isinstance(unsupported, list) or not isinstance(drops, list):
        return None
    return {"unsupported": [u for u in unsupported if isinstance(u, dict)],
            "drops": {str(d).strip().casefold() for d in drops}}


def apply_review(flags: dict[str, Flags], questions: list[dict], lead: dict | None) -> tuple[dict, list[dict]]:
    """Flags with unsupported ones reset; questions without the dropped ones. Never adds a point."""
    if not lead:
        return flags, questions
    resets: dict[str, dict] = {}
    for item in lead["unsupported"]:
        rid, flag = str(item.get("id") or "").strip(), str(item.get("flag") or "").strip()
        if rid in flags and flag in NO_DEDUCTION:
            resets.setdefault(rid, {})[flag] = NO_DEDUCTION[flag]
    kept_flags = {rid: f.model_copy(update=resets[rid]) if rid in resets else f for rid, f in flags.items()}
    kept_questions = [q for q in questions if q["id"] not in lead["drops"]]
    return kept_flags, kept_questions
