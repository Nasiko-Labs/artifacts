"""The code checks: the guarantee, not the prompt.

An item survives only when its snippet is found in the source and every number, date word, email and
URL in its text is in the source too. An owner not written near the snippet becomes "Not stated"; a due
not written near it is blanked. Then dedup, section caps and word caps."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding as g
from items import NOT_STATED, Item, Summary

STATUSES = ("ok", "off_topic", "not_summarizable")
SECTIONS = (("tldr", "tldr"), ("decisions", "decision"), ("actions", "action"))
WORD_CAPS = {"tldr": 30, "decision": 30, "action": 25}
SECTION_CAPS = {"tldr": 5, "decision": 10, "action": 15}
SNIPPET_MAX_CHARS = 200
FIELD_MAX_CHARS = 2_000
GENERIC_OWNERS = frozenset({"we", "us", "our", "ours", "team", "the team", "everyone", "everybody", "all", "all of us",
                            "they", "them", "someone", "somebody", "anyone", "i", "me", "you", "tbd", "tbc", "n/a",
                            "na", "none", "null", "unknown", "unassigned", "not stated", "nobody"})

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_SPACES = re.compile(r"\s+")
_CLAUSE_ENDS = (". ", "; ", ", ", " - ", ": ")


@dataclass(frozen=True)
class Checked:
    items: tuple[Item, ...]
    dropped: int


def parse_summary(data: Any) -> dict | None:
    """The model's JSON, shape-checked; None when it does not match the contract (triggers one repair)."""
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    parsed: dict = {"status": data["status"]}
    for key, _kind in SECTIONS:
        entries = data.get(key) or []
        if not isinstance(entries, list) or not all(isinstance(e, Mapping) for e in entries):
            return None
        parsed[key] = [dict(e) for e in entries]
    return parsed


def clean_text(text: Any, source: str) -> str:
    """One line of model text with images and HTML removed; a link stays only if its URL is in the source."""
    value = _IMAGE.sub("", str(text or "")[:FIELD_MAX_CHARS])
    value = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), value)
    value = _HTML.sub("", value)
    return _SPACES.sub(" ", value.replace("|", "/")).strip()


def check_section(kind: str, entries: Iterable[Mapping[str, Any]], source: str, norm_source: str,
                  prefix: str = "") -> Checked:
    kept: list[Item] = []
    dropped = 0
    for index, entry in enumerate(entries, start=1):
        item = check_item(kind, entry, source, norm_source, f"{prefix}{kind[0]}{index}" if prefix else "")
        if item is None:
            dropped += 1
        else:
            kept.append(item)
    return Checked(tuple(kept), dropped)


def check_item(kind: str, entry: Mapping[str, Any], source: str, norm_source: str, item_id: str = "") -> Item | None:
    text = clean_text(entry.get("text"), source)
    snippet = _SPACES.sub(" ", str(entry.get("snippet") or "")).strip()
    span = g.locate(snippet, norm_source) if text else None
    if span is None or g.ungrounded_tokens(text, norm_source):
        return None
    owner = due = ""
    if kind == "action":
        owner = grounded_owner(clean_text(entry.get("owner"), source), norm_source, span)
        due = grounded_due(clean_text(entry.get("due"), source), norm_source, span)
    return Item(kind=kind, text=cut_words(text, WORD_CAPS[kind]), snippet=cap_snippet(snippet), owner=owner,
                due=due, item_id=item_id)


def grounded_owner(owner: str, norm_source: str, span: tuple[int, int]) -> str:
    if not owner or owner.casefold().strip(" .") in GENERIC_OWNERS:
        return NOT_STATED
    return owner if g.near(owner, norm_source, span, g.OWNER_WINDOW_CHARS, 0) else NOT_STATED


def grounded_due(due: str, norm_source: str, span: tuple[int, int]) -> str:
    if not due or due.casefold() in ("null", "none", "n/a", "not stated"):
        return ""
    return due if g.near(due, norm_source, span, g.DUE_WINDOW_CHARS, g.DUE_WINDOW_CHARS) else ""


def cut_words(text: str, cap: int) -> str:
    """At most `cap` words, cut back to the last clause boundary in the second half, then "…"."""
    words = text.split()
    if len(words) <= cap:
        return text
    head = " ".join(words[:cap])
    cut = max(head.rfind(mark) for mark in _CLAUSE_ENDS)
    if cut >= len(head) // 2:
        head = head[:cut]
    return head.rstrip(" ,;:-.") + "…"


def cap_snippet(snippet: str) -> str:
    return snippet if len(snippet) <= SNIPPET_MAX_CHARS else snippet[:SNIPPET_MAX_CHARS].rstrip() + "…"


def finalize(tldr: Sequence[Item], decisions: Sequence[Item], actions: Sequence[Item], dropped: int,
             notes: Sequence[str] = ()) -> Summary:
    """Dedup each section, then cap it; the counts of both kinds of loss are kept for the reply."""
    sections = {}
    capped = 0
    for kind, items in (("tldr", tldr), ("decision", decisions), ("action", actions)):
        unique = dedup(items)
        sections[kind] = unique[:SECTION_CAPS[kind]]
        capped += max(0, len(unique) - SECTION_CAPS[kind])
    return Summary(tldr=sections["tldr"], decisions=sections["decision"], actions=sections["action"],
                   dropped=dropped, capped=capped, notes=tuple(notes))


def dedup(items: Iterable[Item]) -> tuple[Item, ...]:
    seen: set[tuple[str, str]] = set()
    kept = []
    for item in items:
        key = (g.normalise(item.text).strip(" .…"), g.normalise(item.owner))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return tuple(kept)


def check_summary(parsed: Mapping[str, Any], source: str, prefix: str = "") -> tuple[Checked, Checked, Checked]:
    """(tldr, decisions, actions), each checked against one source or chunk."""
    norm = g.normalise(source)
    tldr, decisions, actions = (check_section(kind, parsed.get(key) or (), source, norm, prefix)
                                for key, kind in SECTIONS)
    return tldr, decisions, actions


def checked_summary(parsed: Mapping[str, Any], source: str, notes: Sequence[str] = ()) -> Summary:
    tldr, decisions, actions = check_summary(parsed, source)
    return finalize(tldr.items, decisions.items, actions.items, tldr.dropped + decisions.dropped + actions.dropped,
                    notes)
