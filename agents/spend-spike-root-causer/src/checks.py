"""Pure: code checks on everything the model returns, and the computed confidence / needs_human.

- Repos, changes, owners and channels: every id, name, title, login and link must appear verbatim in this turn's
  tool output (`seen_text`), or the item is dropped.
- Drafts: every number must come from the checked facts (an allow-list built in code), every link must be one of
  the checked links, no "proven"/"root cause"/"caused by" wording, at most 900 characters. A draft that fails is
  replaced by a draft built in code from the same facts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import detect
from correlate import Candidate, Change
from records import (
    UNKNOWN_OWNER,
    ChangesOut,
    Channel,
    Findings,
    Owner,
    OwnerLookupOut,
    ReposOut,
)

if TYPE_CHECKING:
    from analysis import Analysis

MAX_DRAFT_CHARS = 900
MAX_REPO_PICK = 10
HIGH_IMPACT = 5_000 * 1_000_000
CONFIDENT_LAG_DAYS = 3
_KINDS = {"pr": "pr", "pull_request": "pr", "pull request": "pr", "release": "release", "run": "run",
          "workflow_run": "run", "workflow run": "run", "commit": "commit"}
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PROVEN = re.compile(r"\b(?:caused|causing|root cause|proven|proof|definitely|certainly|because of|is the cause|"
                     r"responsible for|due to)\b", re.IGNORECASE)


def verbatim(value: str | None, seen: str) -> bool:
    return bool(value) and str(value) in seen


# --- lookups -----------------------------------------------------------------------------------------------------

def checked_repos(out: ReposOut | None, seen: str) -> tuple[str, ...]:
    if out is None:
        return ()
    names = [r.full_name.strip() for r in out.repos if verbatim(r.full_name.strip(), seen)]
    return tuple(dict.fromkeys(names))[:MAX_REPO_PICK]


def matching_repos(found: Sequence[str], terms: Sequence[str], limit: int) -> tuple[str, ...]:
    """Repos whose name (the part after the owner) contains one of the search terms, case-insensitive."""
    wanted = [t.casefold() for t in terms if t]
    hits = [r for r in found if any(t in r.rsplit("/", 1)[-1].casefold() for t in wanted)]
    return tuple(hits[:limit])


def checked_changes(out: ChangesOut | None, seen: str, repos: Sequence[str]) -> tuple[Change, ...]:
    if out is None:
        return ()
    kept: list[Change] = []
    for c in out.changes:
        kind = _KINDS.get(c.kind.strip().casefold())
        at = parse_time(c.at)
        cid = str(c.id).strip()
        ok = (kind is not None and at is not None and _repo_known(c.repo, repos) and verbatim(cid, seen)
              and verbatim(c.title, seen) and verbatim(c.link, seen) and verbatim(c.at, seen)
              and (c.author_login is None or verbatim(c.author_login, seen)))
        if ok:
            kept.append(Change(c.repo.strip(), kind, cid, c.title, c.author_login or None, at, c.link))
    return tuple(kept)


def _repo_known(repo: str, repos: Sequence[str]) -> bool:
    short = repo.strip().rsplit("/", 1)[-1].casefold()
    return any(short == r.rsplit("/", 1)[-1].casefold() for r in repos)


def parse_time(text: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def checked_owner(out: OwnerLookupOut | None, seen: str, team: str | None) -> tuple[Owner, tuple[Channel, ...]]:
    if out is None:
        return UNKNOWN_OWNER, ()
    channels = tuple(Channel(c.id, c.name) for c in out.channels
                     if verbatim(c.id, seen) and (not c.name or verbatim(c.name, seen)))[:MAX_REPO_PICK]
    o = out.owner
    if o is None or not verbatim(o.chat_id, seen):
        return UNKNOWN_OWNER, channels
    if not all(verbatim(v, seen) for v in (o.display_name, o.handle, o.link) if v):
        return UNKNOWN_OWNER, channels
    source = "tag" if o.matched == "team" and team else "author"
    return Owner(o.display_name, o.handle, o.chat_id, o.link or "", source), channels


# --- drafts --------------------------------------------------------------------------------------------------------

def _norm_number(text: str) -> str:
    plain = text.replace(",", "")
    whole, _, frac = plain.partition(".")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{frac}" if frac else whole


def allowed_numbers(facts: dict) -> frozenset[str]:
    text = _URL.sub(" ", json.dumps(facts, ensure_ascii=False))
    return frozenset(_norm_number(n) for n in _NUMBER.findall(text))


def allowed_links(facts: dict) -> frozenset[str]:
    return frozenset(_URL.findall(json.dumps(facts, ensure_ascii=False)))


def draft_problem(body: str, facts: dict) -> str | None:
    """Why this draft (model-written or edited by the user) cannot go out as is, or None."""
    text = (body or "").strip()
    if not text:
        return "the draft is empty"
    if len(text) > MAX_DRAFT_CHARS:
        return f"the draft is over {MAX_DRAFT_CHARS} characters"
    links = allowed_links(facts)
    stray = [u.rstrip(".,;") for u in _URL.findall(text) if u.rstrip(".,;") not in links]
    if stray:
        return f"the link {stray[0]} is not from the analysis"
    numbers = allowed_numbers(facts)
    unknown = [n for n in _NUMBER.findall(_URL.sub(" ", text)) if _norm_number(n) not in numbers]
    if unknown:
        return f"the number {unknown[0]} is not from the analysis"
    if _PROVEN.search(text):
        return "it states a cause as proven; a change is only coincident with the onset"
    return None


def template_draft(facts: dict) -> str:
    """The draft built in code from the same facts, used when the model's draft fails a check."""
    s = facts.get("spike") or {}
    owner = facts.get("owner") or "[owner]"
    opening = (f"Hi {owner}, our cloud cost rose {s.get('delta_pct', '')} in the {s.get('window', 'window')} "
               f"({s.get('base_daily', '')} -> {s.get('window_daily', '')} {s.get('currency', '')} per day, about "
               f"{s.get('monthly_impact_if_sustained', '')} a month if sustained), starting around "
               f"{s.get('onset', '')}.")
    lines = [opening, f"{facts.get('chain', '')}."]
    top = (facts.get("candidate_changes_coincident_not_proven") or [None])[0]
    if top:
        lines.append(f"This may be related to {top['kind']} \"{top['title']}\" ({top['link']}), which landed "
                     f"around then.")
    lines.append("Could you confirm, or tell us what changed?")
    return " ".join(x for x in lines if x.strip(" ."))


# --- envelope signals --------------------------------------------------------------------------------------------

def confidence(a: Analysis, f: Findings, ranked: Iterable[Candidate]) -> str:
    if isinstance(a.result, detect.ShortHistory) or a.bad_share_high:
        return "low"
    s, ch = a.spike, a.chain
    if s is None or ch is None:
        return "medium"
    near = any(c.lag_days <= CONFIDENT_LAG_DAYS for c in ranked)
    if s.onset.kind == "step" and ch.team and not ch.stopped_untagged and f.code_checked and near:
        return "high"
    return "medium"


def needs_human(a: Analysis, f: Findings) -> bool:
    s, ch = a.spike, a.chain
    if s is None:
        return bool(a.movers)
    return bool((ch is not None and ch.stopped_untagged) or not f.owner.known or s.monthly_impact >= HIGH_IMPACT)
