"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields). The
catalog's names are exact: `postmortem_markdown` and `action_items[]`; `items` mirrors `action_items`.
`confidence` and `needs_human` come from the code-computed Postmortem, never from the model."""

from __future__ import annotations

import json

import render
from records import Postmortem, action_dict, as_dicts


def build(pm: Postmortem) -> dict:
    actions = [action_dict(a) for a in pm.actions]
    return {
        "summary": pm.summary, "items": actions, "confidence": pm.confidence, "needs_human": pm.needs_human,
        "title": pm.title, "severity": pm.severity, "postmortem_markdown": render.render(pm), "action_items": actions,
        "timeline": [{k: v for k, v in e.items() if k != "stamp"} for e in as_dicts(pm.events)],
        "metrics": dict(pm.metrics), "contributing_factors": as_dicts(pm.factors),
        "open_questions": list(pm.questions), "dropped": pm.dropped,
    }


def as_json_text(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
