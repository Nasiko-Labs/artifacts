"""What the user's message asks for, read in code first: the repositories (or an owner) to scan, pasted
manifests (`=== path ===` blocks), an envelope from an earlier workflow step, JSON output, and the
follow-up commands ("only exposed ones", "show all", "open the PR for acme/web", "open PRs for all
affected"). The advisory itself is read by advisory.py. The model is asked only when code cannot tell,
always with the scope rule and the off-topic sentinel."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

import advisory as adv

MAX_REPOS = 8
MAX_PASTE_CHARS = 100_000

_FILE_EXT = re.compile(r"\.(?:py|js|ts|json|toml|txt|lock|mod|xml|gradle|kts|md|ya?ml)$", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w./@:-])([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_][\w.-]{0,99})(?![\w/-])")
_NOT_OWNERS = frozenset({"and", "or", "pr", "ci", "and/or", "n", "a", "node_modules", "packages", "apps",
                         "services", "src", "refs", "heads", "deps"})
_OWNER = re.compile(r"\b(?:org(?:anization)?|owner|user)\s+[`'\"]?([A-Za-z0-9][A-Za-z0-9-]{0,38})\b"
                    r"|\b(?:repos|repositories)\s+(?:of|in|under|for)\s+[`'\"]?([A-Za-z0-9][A-Za-z0-9-]{0,38})\b",
                    re.IGNORECASE)
_OWNER_STOP = frozenset({"the", "my", "our", "a", "an", "this", "that", "all", "every", "your"})
_BLOCK = re.compile(r"^===\s*(\S[^=]*?)\s*===\s*$", re.MULTILINE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_EXPOSED_ONLY = re.compile(r"\bonly\s+(?:the\s+)?(?:exposed|affected)\b|\b(?:exposed|affected)\s+only\b|"
                           r"\bhide\s+(?:the\s+)?not[ -]affected\b", re.IGNORECASE)
_SHOW_ALL = re.compile(r"\b(?:show|list|include)\s+(?:me\s+)?(?:all|every(?:thing)?|the\s+not[ -]affected|"
                       r"not[ -]affected)\b", re.IGNORECASE)
_PR = re.compile(r"\b(?:open|create|make|raise|draft|prepare|send)\b.*\b(?:pr|prs|pull requests?|upgrade|bump)\b"
                 r"|\b(?:upgrade|bump)\s+(?:it|them)\b|\bfix\s+(?:it|them)\b", re.IGNORECASE)
_ALL = re.compile(r"\b(?:all|every|each)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str                      # filter | pr | ""
    show_all: bool = False
    repos: tuple[str, ...] = ()
    every: bool = False


@dataclass(frozen=True)
class Request:
    repos: tuple[str, ...] = ()
    owner: str = ""
    pasted: tuple[tuple[str, str], ...] = ()   # (path, text) of pasted manifests
    cut: tuple[str, ...] = ()                  # repositories beyond the cap


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def repos_in(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for m in _REPO.finditer(text or ""):
        owner, name = m.group(1), m.group(2).rstrip(".,;:")
        if owner.casefold() in _NOT_OWNERS or owner[0].isdigit() and owner.isdigit():
            continue
        if _FILE_EXT.search(name) or name.casefold().endswith(".git") or name.casefold() in ("me", "it"):
            continue
        found.append(f"{owner}/{name}")
    return tuple(dict.fromkeys(found))


def owner_in(text: str) -> str:
    for m in _OWNER.finditer(text or ""):
        name = m.group(1) or m.group(2)
        if name and name.casefold() not in _OWNER_STOP:
            return name
    return ""


def pasted_manifests(text: str) -> tuple[tuple[str, str], ...]:
    """`=== path ===` blocks: (path, body) for each block whose path names a manifest or lockfile."""
    marks = list(_BLOCK.finditer(text or ""))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        path = m.group(1).strip().strip("`")
        out.append((path, text[m.end():end].strip("\n") + "\n"))
    return tuple(out)


def advisory_text(text: str) -> str:
    """The message without its pasted manifest blocks (so a manifest's versions are never read as a range)."""
    marks = list(_BLOCK.finditer(text or ""))
    return text[:marks[0].start()] if marks else text


def request_in(text: str) -> Request:
    pasted = pasted_manifests(text)
    scope = advisory_text(text)
    names = repos_in(scope)
    return Request(names[:MAX_REPOS], "" if names else owner_in(scope), pasted, names[MAX_REPOS:])


def envelope(text: str) -> tuple[adv.Advisory | None, tuple[str, ...]] | None:
    """An envelope from an earlier step: a JSON object (fenced or not) with an `advisory` object and/or a
    `repos` list. None when the text is not one."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, Mapping) or not ("advisory" in data or "repos" in data):
        return None
    advisory = adv.from_mapping(data["advisory"]) if isinstance(data.get("advisory"), Mapping) else None
    raw = data.get("repos") if isinstance(data.get("repos"), list) else []
    names = [str(r.get("repo") if isinstance(r, Mapping) else r) for r in raw]
    repos = tuple(dict.fromkeys(r for n in names for r in repos_in(n)))
    return advisory, repos[:MAX_REPOS]


def command(text: str, known: tuple[str, ...]) -> Command:
    """A follow-up recognised by code, or Command("") when the model has to read it."""
    if _EXPOSED_ONLY.search(text or ""):
        return Command("filter", show_all=False)
    if _SHOW_ALL.search(text or ""):
        return Command("filter", show_all=True)
    if _PR.search(text or ""):
        named = tuple(r for r in repos_in(text) if r.casefold() in {k.casefold() for k in known})
        return Command("pr", repos=named, every=bool(_ALL.search(text)) and not named)
    return Command("")


def is_new_scan(text: str, report_repos: tuple[str, ...]) -> bool:
    """The message brings new input: a pasted advisory, manifests, an envelope, or repositories not scanned."""
    if pasted_manifests(text) or envelope(text) is not None:
        return True
    if adv.parse_text(advisory_text(text)).advisory.package and adv.parse_text(text).signal:
        return True
    known = {r.casefold() for r in report_repos}
    names = repos_in(text)
    return bool(names) and not _PR.search(text) and any(n.casefold() not in known for n in names)
