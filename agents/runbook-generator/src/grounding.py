"""Pure: keeps only facts whose snippet (and command) appear in the user's text, and decides each step's
provenance: `input` only when its command (or its cited facts) can be found in that text."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from model import FACT_KINDS, INPUT, SUGGESTED, Fact
from placeholders import Grounder, normalise

MAX_SNIPPET_CHARS = 400
MAX_FACT_TEXT_CHARS = 300
COMMAND_KEY_TOKENS = 3


@dataclass(frozen=True)
class CheckedFacts:
    facts: tuple[Fact, ...]
    service: str
    summary: str
    dropped: int


def in_text(value: str, haystack_normalised: str) -> bool:
    needle = normalise(value)
    return bool(needle) and needle in haystack_normalised


def check_facts(raw: dict, source: str, start: int = 1) -> CheckedFacts:
    """Facts from one extract answer, numbered from `start`; ungrounded ones are dropped and counted."""
    hay = normalise(source)
    grounder = Grounder(source)
    kept: list[Fact] = []
    dropped = 0
    for item in raw.get("facts") or ():
        fact = _fact(item, hay, start + len(kept), grounder)
        if fact is None:
            dropped += 1
        else:
            kept.append(fact)
    service = str(raw.get("service") or "").strip()
    return CheckedFacts(tuple(kept), service if in_text(service, hay) else "unknown",
                        str(raw.get("summary") or "").strip()[:MAX_FACT_TEXT_CHARS], dropped)


def _fact(item: object, hay: str, number: int, grounder: Grounder) -> Fact | None:
    if not isinstance(item, dict):
        return None
    snippet = str(item.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS]
    text = grounder.ground(str(item.get("text") or "").strip()[:MAX_FACT_TEXT_CHARS])
    if not text or not in_text(snippet, hay):
        return None
    command = str(item.get("command") or "").strip().strip("`").removeprefix("$ ").strip() or None
    if command and not in_text(command, hay):
        command = None  # a command not written in the text is not a fact
    kind = str(item.get("kind") or "other").strip().casefold()
    return Fact(f"f{number}", kind if kind in FACT_KINDS else "other", text, snippet, command)


def merge(parts: Iterable[CheckedFacts]) -> CheckedFacts:
    """Map results merged: facts deduped by (kind, command or text), renumbered f1..fn."""
    seen: set[tuple[str, str]] = set()
    facts: list[Fact] = []
    service, summary, dropped = "unknown", "", 0
    for part in parts:
        dropped += part.dropped
        service = service if service != "unknown" else part.service
        summary = summary or part.summary
        for fact in part.facts:
            key = (fact.kind, normalise(fact.command or fact.text))
            if key in seen:
                continue
            seen.add(key)
            facts.append(Fact(f"f{len(facts) + 1}", fact.kind, fact.text, fact.snippet, fact.command))
    return CheckedFacts(tuple(facts), service, summary, dropped)


def command_key(command: str) -> str:
    return " ".join(normalise(command).split()[:COMMAND_KEY_TOKENS])


def provenance(command: str | None, fact_ids: Iterable[str], facts: dict[str, Fact], source: str) -> str:
    """`input` when the step's command (first tokens) appears in a fact or the text, or it cites real facts and
    has no command; otherwise `suggested`."""
    cited = [facts[i] for i in fact_ids if i in facts]
    if command:
        key = command_key(command)
        evidence = [normalise(f.command or "") + " " + normalise(f.snippet) for f in (cited or facts.values())]
        return INPUT if any(key in e for e in evidence) or (cited and key in normalise(source)) else SUGGESTED
    return INPUT if cited else SUGGESTED
