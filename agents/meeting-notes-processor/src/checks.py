"""The code checks: the guarantee, not the prompt.

Every topic bullet, decision and action survives only when its quote is found (filler-tolerant) in the
turn(s) it cites; a quote found in exactly one other turn is re-pointed there, in several or none it is
dropped. Every number, date word, email and URL in its own words must be in the transcript too. Owners,
dues and kinds go through owners.py. Then dedup and caps."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import grounding as g
import owners
from items import Action, Bullet, Decision, Topic, Turn

STATUSES = ("ok", "off_topic", "not_meeting")
WORD_CAPS = {"title": 8, "bullet": 25, "decision": 30, "action": 25}
CAPS = {"topics": 8, "bullets": 5, "decisions": 15, "actions": 25}
MAX_CITED_TURNS = 3
QUOTE_MAX_CHARS = 200
FIELD_MAX_CHARS = 2_000

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_SPACES = re.compile(r"\s+")
_CLAUSE_ENDS = (". ", "; ", ", ", " - ", ": ")


@dataclass
class Index:
    """The parsed turns, normalised once for every check of one turn."""

    turns: Sequence[Turn]
    speakers: Sequence[str]
    by_id: dict[str, int] = field(default_factory=dict)
    norms: list[str] = field(default_factory=list)
    source: str = ""
    norm_source: str = ""

    def __post_init__(self) -> None:
        self.by_id = {t.id: i for i, t in enumerate(self.turns)}
        self.norms = [g.normalise(t.text) for t in self.turns]
        self.source = "\n".join(f"{t.time} {t.speaker}: {t.text}" for t in self.turns)
        self.norm_source = g.normalise(self.source)


def parse_notes(data: Any) -> dict | None:
    """The model's JSON, shape-checked; None when it does not match the contract (one repair follows)."""
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    parsed: dict = {"status": data["status"]}
    for key in ("topics", "decisions", "actions"):
        entries = data.get(key) or []
        if not isinstance(entries, list) or not all(isinstance(e, Mapping) for e in entries):
            return None
        parsed[key] = [dict(e) for e in entries]
    return parsed


def clean_text(text: Any, source: str) -> str:
    """One line of model text without images or HTML; a link stays only if its URL is in the source."""
    value = _IMAGE.sub("", str(text or "")[:FIELD_MAX_CHARS])
    value = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), value)
    return _SPACES.sub(" ", _HTML.sub("", value).replace("|", "/")).strip()


def cut_words(text: str, cap: int) -> str:
    words = text.split()
    if len(words) <= cap:
        return text
    head = " ".join(words[:cap])
    cut = max(head.rfind(mark) for mark in _CLAUSE_ENDS)
    return (head[:cut] if cut >= len(head) // 2 else head).rstrip(" ,;:-.") + "…"


def cap_quote(quote: str) -> str:
    quote = _SPACES.sub(" ", quote).strip()
    return quote if len(quote) <= QUOTE_MAX_CHARS else quote[:QUOTE_MAX_CHARS].rstrip() + "…"


def find_quote(quote: str, cited: Sequence[str], index: Index) -> tuple[str, ...] | None:
    """The turn ids the quote is in: the cited ones when it is there, else the one other turn holding it."""
    known = [c for c in cited if c in index.by_id][:MAX_CITED_TURNS]
    if known and g.locate(quote, " ".join(index.norms[index.by_id[c]] for c in known)) is not None:
        return tuple(known)
    hits = [index.turns[i].id for i, norm in enumerate(index.norms) if g.locate(quote, norm) is not None]
    return (hits[0],) if len(hits) == 1 else None


def grounded_text(raw: Any, cap: int, index: Index) -> str:
    text = clean_text(raw, index.source)
    return "" if not text or g.ungrounded_tokens(text, index.norm_source) else cut_words(text, cap)


def check_decision(entry: Mapping[str, Any], index: Index, item_id: str = "") -> Decision | None:
    text, quote = grounded_text(entry.get("text"), WORD_CAPS["decision"], index), str(entry.get("quote") or "")
    found = find_quote(quote, [str(entry.get("turn") or "")], index) if text else None
    return Decision(text, found[0], cap_quote(quote), item_id) if found else None


def check_action(entry: Mapping[str, Any], index: Index, item_id: str = "") -> Action | None:
    task, quote = grounded_text(entry.get("task"), WORD_CAPS["action"], index), str(entry.get("quote") or "")
    found = find_quote(quote, [str(entry.get("turn") or "")], index) if task else None
    if not found:
        return None
    at = index.by_id[found[0]]
    owner, accepted = owners.resolve(entry.get("owner_ref"), clean_text(entry.get("owner_name"), index.source), quote,
                                     at, index.turns, index.speakers)
    kind = "follow_up" if accepted is False else owners.kind_of(entry.get("kind"), task)
    due = owners.grounded_due(clean_text(entry.get("due"), index.source), quote, at, index.turns)
    return Action(task, kind, owner, due, found[0], cap_quote(quote), item_id)


def check_topic(entry: Mapping[str, Any], index: Index, item_id: str = "") -> tuple[Topic | None, int]:
    """(topic or None, bullets dropped)."""
    title = grounded_text(entry.get("title"), WORD_CAPS["title"], index)
    raw = entry.get("bullets") if isinstance(entry.get("bullets"), list) else []
    bullets, dropped = [], 0
    for n, b in enumerate(b for b in raw if isinstance(b, Mapping)):
        text, snippet = grounded_text(b.get("text"), WORD_CAPS["bullet"], index), str(b.get("snippet") or "")
        cited = [str(t) for t in b.get("turns") or []] if isinstance(b.get("turns"), list) else []
        found = find_quote(snippet, cited, index) if text else None
        if found:
            bullets.append(Bullet(text, found, cap_quote(snippet), f"{item_id}.{n + 1}" if item_id else ""))
        else:
            dropped += 1
    if not title or not bullets:
        return None, dropped + (len(bullets) if not title else 0)
    return Topic(title, tuple(bullets[:CAPS["bullets"]]), item_id), dropped


def check_all(parsed: Mapping[str, Any], index: Index, prefix: str = "") -> tuple[list, list, list, int]:
    """(topics, decisions, actions, dropped) for one model answer."""
    def ident(kind: str, n: int) -> str:
        return f"{prefix}{kind}{n}" if prefix else ""

    dropped, topics = 0, []
    for n, entry in enumerate(parsed.get("topics") or (), start=1):
        topic, lost = check_topic(entry, index, ident("t", n))
        dropped += lost
        topics += [topic] if topic else []
    decisions = [check_decision(e, index, ident("d", n)) for n, e in enumerate(parsed.get("decisions") or (), 1)]
    actions = [check_action(e, index, ident("a", n)) for n, e in enumerate(parsed.get("actions") or (), 1)]
    dropped += decisions.count(None) + actions.count(None)
    return topics, [d for d in decisions if d], [a for a in actions if a], dropped


def dedup(items: Iterable, text_of, owner_of=lambda _i: "") -> list:
    seen: set[tuple[str, str]] = set()
    kept = []
    for item in items:
        key = (g.normalise(text_of(item)).strip(" .…"), g.normalise(owner_of(item)))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def finalize(topics: Sequence[Topic], decisions: Sequence[Decision], actions: Sequence[Action]) -> tuple:
    """(topics, decisions, actions, capped) after dedup and caps."""
    t = dedup(topics, lambda x: x.title)
    d = dedup(decisions, lambda x: x.text)
    a = dedup(actions, lambda x: x.task, lambda x: x.owner)
    capped = sum(max(0, len(x) - CAPS[k]) for x, k in ((t, "topics"), (d, "decisions"), (a, "actions")))
    return tuple(t[:CAPS["topics"]]), tuple(d[:CAPS["decisions"]]), tuple(a[:CAPS["actions"]]), capped
