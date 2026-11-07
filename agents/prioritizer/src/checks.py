"""Pure: code checks on the model's ratings. Ratings are validated field by field; `impact_why` must share a
word with the task or a goal and carry no number the user didn't write; `delegate_to` must appear in the task."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

WHY_MAX_WORDS = 15
RUBRIC_LABELS = {
    5: "high stakes: a goal, customers, revenue or legal exposure",
    4: "strongly supports a goal or many people",
    3: "useful; supports a goal indirectly or a few people",
    2: "nice to have, internal convenience",
    1: "no stated benefit",
}
DEFAULT_RATING = {"impact": 3, "impact_why": "", "effort": 3, "delegable": False, "delegate_to": None,
                  "defaulted": True}
_STOP = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "into", "your", "our", "their", "its", "are", "was",
    "were", "will", "can", "has", "have", "had", "not", "but", "all", "any", "you", "who", "how", "why", "what",
    "when", "out", "per", "via", "about", "over", "under", "more", "less", "very", "just", "also", "than", "then",
    "them", "they", "it's",
})
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'-]*")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def plain(text: str) -> str:
    """One line of plain text: tags, images and link targets removed, markdown emphasis dropped."""
    no_markup = _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or "")))
    return " ".join(no_markup.replace("**", "").replace("`", "").split())


def content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").casefold())
            if t not in _STOP and (len(t) >= 3 or any(c.isdigit() for c in t))}


def clean_why(why: str, impact: int, task: str, goals: Sequence[str]) -> str:
    """The model's reason when it is grounded in the task or a goal, else the rubric label for `impact`."""
    text = " ".join(plain(why).split()[:WHY_MAX_WORDS]).rstrip(" .,;:")
    grounds = " ".join([task, *goals])
    allowed_numbers = set(_NUMBER.findall(grounds))
    grounded = bool(content_tokens(text) & content_tokens(grounds))
    if not text or not grounded or set(_NUMBER.findall(text)) - allowed_numbers:
        return RUBRIC_LABELS.get(impact, RUBRIC_LABELS[3])
    return text


def safe_delegate(name: Any, task: str) -> str | None:
    """The delegate's name only when it appears verbatim (whole word) in the task text."""
    if not isinstance(name, str) or not name.strip():
        return None
    clean = " ".join(name.split())
    return clean if re.search(rf"(?<!\w){re.escape(clean)}(?!\w)", task or "", re.IGNORECASE) else None


def _scale(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5 else None


def valid_rating(raw: Any, needs_effort: bool) -> dict | None:
    """One rating, field by field; None when any required field is missing or out of range."""
    if not isinstance(raw, dict):
        return None
    impact, effort = _scale(raw.get("impact")), _scale(raw.get("effort"))
    if impact is None or (needs_effort and effort is None) or not isinstance(raw.get("delegable"), bool):
        return None
    why = raw.get("impact_why")
    to = raw.get("delegate_to")
    return {"impact": impact, "impact_why": why if isinstance(why, str) else "", "effort": effort or 3,
            "delegable": raw["delegable"], "delegate_to": to if isinstance(to, str) else None}


def valid_batch(data: Any, ids: Iterable[str], needs_effort: set[str]) -> tuple[dict[str, dict], bool]:
    """(the valid ratings by id, whether the whole answer met the contract)."""
    expected = list(ids)
    if not isinstance(data, dict) or not isinstance(data.get("ratings"), list):
        return {}, False
    found: dict[str, dict] = {}
    for raw in data["ratings"]:
        task_id = raw.get("id") if isinstance(raw, dict) else None
        if task_id in expected and task_id not in found:
            rating = valid_rating(raw, task_id in needs_effort)
            if rating is not None:
                found[task_id] = rating
    complete = len(found) == len(expected) and len(data["ratings"]) == len(expected)
    return found, complete
