"""Linking (live only, not part of the copied method): one tracker tool loop searches existing issues for the top
8 shown themes; code verifies every issue against the tool output and scores it against each theme. The model
never decides a link: a wrong link is worse than no link. Closed issues are kept and marked."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

import prompts
import records
from checks import Seen, checked_url
from deps import TurnDeps
from reader import TurnReader
from records import LinkedIssue, RawIssues
from sourcing import SourceRun, read_source
from themes import ThemeStats

MIN_SCORE, MIN_SHARED, MAX_LINKS = 0.34, 2, 3
_TOKEN = re.compile(r"[a-z0-9]+")
STOP_WORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "cannot", "could", "do", "does", "for", "from", "has", "have", "how", "i", "in", "into", "is", "it", "its", "no", "not", "of", "on", "or", "our", "so", "that", "the", "their", "them", "there", "this", "to", "too", "us", "via", "was", "we", "were", "when", "with", "without", "you", "your", "more", "less", "very", "please", "need", "needs", "want", "wants", "should", "would", "add", "support"])


def tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").casefold()) if t not in STOP_WORDS and len(t) > 1}


def search_phrases(stats: Sequence[ThemeStats], labels_of: Mapping[str, Sequence[str]], limit: int) -> list[str]:
    phrases = []
    for stat in [s for s in stats if s.shown][:limit]:
        words = Counter(t for label in labels_of.get(stat.id, ()) for t in tokens(label))
        top = " ".join(w for w, _ in words.most_common(3))
        phrases.append(stat.name if not top or set(top.split()) <= tokens(stat.name) else f"{stat.name} | {top}")
    return phrases


def verified_issues(parsed: RawIssues, seen: Seen) -> list[dict]:
    out, ids = [], set()
    for raw in parsed.issues:
        ref, title = str(raw.id).strip(), " ".join((raw.title or "").split())
        if not seen.has_exact(ref) or not seen.has(title) or ref in ids:
            continue
        ids.add(ref)
        state = (raw.state or "").strip()
        out.append({"id": ref, "title": title, "url": checked_url(raw.url, seen),
                    "state": state if seen.has(state) else "unknown"})
    return out


def score(name: str, labels: Sequence[str], title: str) -> tuple[float, int]:
    own = tokens(name)
    shared = (own | {t for label in labels for t in tokens(label)}) & tokens(title)
    return (round(len(shared) / len(own), 2) if own else 0.0), len(shared)


def links_for(stat: ThemeStats, labels: Sequence[str], issues: Sequence[dict]) -> list[LinkedIssue]:
    scored = []
    for issue in issues:
        value, shared = score(stat.name, labels, issue["title"])
        if value >= MIN_SCORE and shared >= MIN_SHARED:
            scored.append(LinkedIssue(issue["id"], issue["title"], issue["url"], issue["state"], value))
    return sorted(scored, key=lambda i: (-i.score, i.id))[:MAX_LINKS]


def link_action(links: Sequence[LinkedIssue]) -> str:
    open_links = [i for i in links if not i.closed]
    if open_links:
        return f"Add these customers as evidence on {open_links[0].id}"
    if links:
        return f"Check whether {links[0].id} actually solved this"
    return "No existing issue: consider opening one (ask me to draft it)"


async def link_step(deps: TurnDeps, turn: TurnReader, stats: Sequence[ThemeStats],
                    labels_of: Mapping[str, Sequence[str]], team: str = "") -> tuple[list[dict] | None, SourceRun]:
    """The verified issues the searches returned, or None when the tracker was not read."""
    phrases = search_phrases(stats, labels_of, deps.config.max_linked_themes)
    if not phrases:
        return [], SourceRun(read=True)
    scope = f" Limit the searches to the team {team!r}." if team else ""
    cfg = deps.config
    run = await read_source(deps, turn, "link", prompts.link_system(phrases), "Search the tracker now." + scope,
                            cfg.link_loop_calls, cfg.link_tool_calls, records.parser(RawIssues))
    if run.parsed is None:
        return None, run
    return verified_issues(run.parsed, turn.seen), run
