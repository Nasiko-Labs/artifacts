"""Pure: the reply text around the memo markdown (open items, notes, review flag, what to say next), the
JSON form for workflows, and the recommendation-only view."""

from __future__ import annotations

import json
from collections.abc import Sequence

import prompts


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def reply(markdown: str, open_items: Sequence[str], notes: Sequence[str], envelope: dict) -> str:
    blocks = [markdown]
    if open_items:
        blocks.append(f"_Open items: {', '.join(open_items)}. Fill these in or add data (\"add data: ...\")._")
    blocks += [f"_{n}_" for n in notes if n]
    if envelope.get("needs_human"):
        why = ("a close call" if envelope["recommendation"]["close_call"] else
               "confidence is low" if envelope["confidence"] == "low" else "open items remain")
        blocks.append(f"_Review before sign-off: {why} (confidence {envelope['confidence']})._")
    blocks.append(f"_{prompts.FOLLOWUP_HELP}_")
    return "\n\n".join(blocks)


def recommendation_only(line: str) -> str:
    return line + f"\n\n_{prompts.FOLLOWUP_HELP}_"
