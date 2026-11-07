"""Pure: which of the three plan formats a paste is, the parse, and the plan-integrity checks shared by all
formats (the plan's own summary counts against the parsed changes, drift, tainted resources)."""

from __future__ import annotations

import re
from dataclasses import replace

import parse_human
import parse_json
from changes import ParsedPlan

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HUMAN = re.compile(r"Terraform will perform the following actions|^\s*# \S+\.\S+ (?:will|must) be|"
                    r"^\s*No changes\. (?:Your infrastructure|Infrastructure)", re.MULTILINE)
STREAM_MIN_SHARE = 0.5


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def detect(text: str) -> str | None:
    """`show-json`, `stream-json`, `human`, or None when the text is not a Terraform plan."""
    data = parse_json.load_object(text) if "{" in text else None
    if data is not None and ("resource_changes" in data or "format_version" in data):
        return "show-json"
    lines = [line for line in text.splitlines() if line.strip()]
    objects = parse_json.stream_objects(text)
    stream = [o for o in objects if "@level" in o or "type" in o]
    if lines and stream and len(stream) >= STREAM_MIN_SHARE * len(lines) and \
            any(o.get("type") in ("planned_change", "change_summary") for o in stream):
        return "stream-json"
    if _HUMAN.search(text):
        return "human"
    return None


def parse(text: str) -> ParsedPlan | None:
    clean = strip_ansi(text)
    kind = detect(clean)
    if kind == "show-json":
        plan = parse_json.parse_show(parse_json.load_object(clean) or {})
    elif kind == "stream-json":
        plan = parse_json.parse_stream(parse_json.stream_objects(clean))
    elif kind == "human":
        plan = parse_human.parse(clean)
    else:
        return None
    return _integrity(plan)


def computed_counts(plan: ParsedPlan) -> dict:
    counts = {k: 0 for k in ("add", "change", "destroy", "replace", "read", "noop", "import", "move", "forget")}
    for change in plan.changes:
        key = {"create": "add", "update": "change", "delete": "destroy"}.get(change.action, change.action)
        counts[key] += 1
    counts["total"] = sum(1 for c in plan.changes if c.action not in ("noop", "read"))
    counts["drift_notes"] = len(plan.drift)
    return counts


def _integrity(plan: ParsedPlan) -> ParsedPlan:
    caveats = list(plan.caveats)
    hard = plan.hard
    if plan.stated is not None and not any("cut off" in c for c in caveats):
        mismatch = _mismatch(plan)
        if mismatch:
            caveats.append(mismatch)
            hard = True
    if plan.drift:
        caveats.append(f"{len(plan.drift)} object(s) changed outside of Terraform (drift); listed, not counted.")
    tainted = [c.address for c in plan.changes if c.reason == "tainted" or "tainted" in c.reason.casefold()]
    if tainted:
        caveats.append(f"Tainted, so replaced: {', '.join(tainted[:5])}.")
    return replace(plan, caveats=tuple(caveats), hard=hard)


def _mismatch(plan: ParsedPlan) -> str:
    counts = computed_counts(plan)
    parsed = {"add": counts["add"] + counts["replace"], "change": counts["change"],
              "destroy": counts["destroy"] + counts["replace"], "import": counts["import"]}
    stated = plan.stated or {}
    wrong = [k for k in ("add", "change", "destroy", "import") if k in stated and stated[k] != parsed[k]]
    if not wrong:
        return ""
    said = ", ".join(f"{stated[k]} to {k}" for k in wrong)
    found = ", ".join(f"{parsed[k]} to {k}" for k in wrong)
    return f"The plan's summary says {said}, but I found {found} in the pasted text: some of it may be missing."
