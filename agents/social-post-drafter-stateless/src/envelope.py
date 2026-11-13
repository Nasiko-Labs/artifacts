"""Pure: the output envelope (every field computed in code) and a previous step's envelope read as
material (its `summary` and `items[].title` / `items[].summary`)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from fixes import is_over_length
from models import Variant
from rules import PLATFORM_LABELS

MIN_GROUNDED_FACTS = 2
_FENCED = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def confidence(variants: Sequence[Variant], repaired: bool) -> str:
    holes = _placeholders(variants)
    flags = sum(len(v.flags) for v in variants)
    facts = {f.source for v in variants for f in v.facts}
    if any(is_over_length(v) for v in variants) or len(holes) > 2 or len(facts) < MIN_GROUNDED_FACTS:
        return "low"
    if holes or flags or repaired:
        return "medium"
    return "high"


def _placeholders(variants: Sequence[Variant]) -> list[str]:
    return list(dict.fromkeys(p for v in variants for p in v.placeholders))


def summary(variants: Sequence[Variant]) -> str:
    groups: dict[str, list[Variant]] = {}
    for v in variants:
        groups.setdefault(v.platform, []).append(v)
    parts = [f"{len(vs)} {PLATFORM_LABELS[p]} variant{'s' if len(vs) != 1 else ''} ({_counts(vs)})"
             for p, vs in groups.items()]
    holes = len(_placeholders(variants))
    tail = f"; {holes} placeholder{'s' if holes != 1 else ''} to fill." if holes else "; nothing to fill in."
    return "; ".join(parts) + tail


def _counts(variants: Sequence[Variant]) -> str:
    if variants[0].platform == "x_thread":
        sizes = [f"{len(v.posts)} posts" for v in variants]
    else:
        unit = "characters" if variants[0].platform == "linkedin" else "weighted characters"
        sizes = [f"{v.chars[0]:,}" for v in variants]
        sizes[-1] += f" {unit}"
    return sizes[0] if len(sizes) == 1 else ", ".join(sizes[:-1]) + " and " + sizes[-1]


def build(variants: Sequence[Variant], platform: str, goal: str, voice: Mapping[str, Any], repaired: bool) -> dict:
    level = confidence(variants, repaired)
    holes = _placeholders(variants)
    return {"summary": summary(variants), "items": [v.to_dict() for v in variants], "confidence": level,
            "needs_human": level == "low" or bool(holes), "platform": platform, "goal": goal,
            "voice": dict(voice), "placeholders": holes}


def fenced(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def material_from(text: str) -> tuple[str, str] | None:
    """(material, the words around it) when the message carries another agent's envelope, else None."""
    raw = text or ""
    match = _FENCED.search(raw)
    start, end = (match.start(1), match.end(1)) if match else (raw.find("{"), raw.rfind("}") + 1)
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    lines = [data["summary"].strip()] if isinstance(data.get("summary"), str) and data["summary"].strip() else []
    for item in data.get("items") or ():
        if isinstance(item, Mapping):
            bits = [str(item[k]).strip() for k in ("title", "summary") if isinstance(item.get(k), str)]
            if any(bits):
                lines.append("- " + ": ".join(b for b in bits if b))
    if not lines:
        return None
    outer = (match.start(), match.end()) if match else (start, end)
    return "\n".join(lines), f"{raw[:outer[0]]} {raw[outer[1]:]}".strip()
