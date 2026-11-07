"""Pure: what the user asked for, parsed in code. Repositories (`owner/repo`, up to 5), the stale
threshold, drafts, the load window, "as json", an envelope from a previous step, and the follow-up
sentinels (nudge #N, nudge all stale, why #N, only <repo>, who is overloaded, refresh)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from view import MAX_STALE_DAYS, MAX_WINDOW_DAYS, MIN_STALE_DAYS, MIN_WINDOW_DAYS, clamp

MAX_REPOS = 5
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
         "a": 1, "a couple of": 2, "a few": 3}
_NUM = r"(\d{1,3}|" + "|".join(sorted((re.escape(w) for w in WORDS), key=len, reverse=True)) + ")"
_REPO = re.compile(r"(?<![\w./@-])([A-Za-z0-9][\w.-]*)/([\w.-]*[A-Za-z0-9_])(?![\w/-])")
_NOT_REPO = frozenset({"and/or", "either/or", "on/off", "yes/no", "read/write", "n/a", "w/o", "pr/mr"})
_STALE = re.compile(rf"(?:stale|quiet|idle|inactive|stuck)\s*(?:after|for|over|at|=|>=|>)?\s*(?:more than\s*)?"
                    rf"{_NUM}\s*\+?\s*(?:d\b|days?\b)", re.IGNORECASE)
_STALE_AFTER = re.compile(rf"{_NUM}\s*\+?\s*days?\s*(?:stale|quiet|idle|old|without)", re.IGNORECASE)
_WINDOW = re.compile(rf"(?:last|past)\s+{_NUM}\s+days", re.IGNORECASE)
_DRAFTS_ON = re.compile(r"\b(?:includ\w*|with|show|count)\s+(?:the\s+)?drafts?\b|\bdrafts?\s+too\b", re.IGNORECASE)
_DRAFTS_OFF = re.compile(r"\b(?:exclud\w*|without|no|hide|skip)\s+(?:the\s+)?drafts?\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_NUDGE_ALL = re.compile(r"\bnudge\s+(?:all|every|each)\b", re.IGNORECASE)
_NUDGE_N = re.compile(r"\bnudge\s+(?:pr\s*|#|number\s*)?#?(\d{1,6})\b", re.IGNORECASE)
_NUDGE_TOP = re.compile(r"\bnudge\s+(?:the\s+)?(?:stalest|oldest|top|first)\b", re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b.*?#\s*(\d{1,6})|\bwhy\s+(?:is\s+)?(?:pr\s*)?(\d{1,6})\b", re.IGNORECASE)
_ONLY = re.compile(r"\bonly\s+(?:show\s+)?([\w.-]+/[\w.-]+|[\w.-]+)\b", re.IGNORECASE)
_LOAD = re.compile(r"\boverload\w*|\breview load\b|\bwho is (?:busy|idle)\b|\bload\b|\bidle reviewers?\b",
                   re.IGNORECASE)
_REFRESH = re.compile(r"\b(?:refresh|re-?check|check again|run again|fetch again|reload|update it)\b",
                      re.IGNORECASE)
_SHOW = re.compile(r"\b(?:show|list)\s+(?:the\s+|me\s+the\s+)?(?:queue|stale|list)\b|\bqueue again\b", re.IGNORECASE)
_APPROVAL_WORDS = re.compile(r"^(?:post(?: it| nudge)?|skip|approve|yes|send)\.?$", re.IGNORECASE)
_TOPIC = re.compile(r"\b(?:prs?|pull|merge requests?|reviews?|reviewers?|stale|stuck|queue|load|nudge|blocked|"
                    r"overloaded|idle|drafts?|repos?|repositor\w*|approv\w*|quiet)\b", re.IGNORECASE)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.casefold()]


@dataclass(frozen=True)
class Parsed:
    repos: tuple[str, ...] = ()
    extra_repos: int = 0                 # named beyond MAX_REPOS
    stale_days: int | None = None
    include_drafts: bool | None = None
    window_days: int | None = None
    as_json: bool = False
    on_topic_words: bool = False


def parse(text: str) -> Parsed:
    envelope = envelope_input(text)
    if envelope is not None:
        return envelope
    repos = repos_in(text)
    stale = _STALE.search(text) or _STALE_AFTER.search(text)
    window = _WINDOW.search(text)
    drafts = True if _DRAFTS_ON.search(text) else False if _DRAFTS_OFF.search(text) else None
    return Parsed(tuple(repos[:MAX_REPOS]), max(0, len(repos) - MAX_REPOS),
                  clamp(_number(stale.group(1)), MIN_STALE_DAYS, MAX_STALE_DAYS) if stale else None, drafts,
                  clamp(_number(window.group(1)), MIN_WINDOW_DAYS, MAX_WINDOW_DAYS) if window else None,
                  bool(_JSON.search(text)), bool(_TOPIC.search(text)) or _only_repos(text, repos))


def _only_repos(text: str, repos: list[str]) -> bool:
    """A message that is nothing but repository names (the answer to "Which repositories?")."""
    rest = text or ""
    for repo in repos:
        rest = rest.replace(repo, " ")
    return bool(repos) and not re.sub(r"[\s,;&.]+|\band\b", "", rest)


def repos_in(text: str) -> list[str]:
    found = []
    for owner, name in _REPO.findall(text or ""):
        full = f"{owner}/{name}"
        if full.casefold() in _NOT_REPO or owner.isdigit() or name.isdigit() or "." in owner[:1]:
            continue
        found.append(full)
    return list(dict.fromkeys(found))


def envelope_input(text: str) -> Parsed | None:
    """A previous step's envelope (or `{"repos": [...]}`): reads `repos[]`, `stale_days`, `include_drafts`."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    repos = [str(r) for r in data.get("repos") or () if isinstance(r, str)]
    repos += [str(p.get("repo")) for p in data.get("stale_prs") or () if isinstance(p, dict) and p.get("repo")]
    repos = [r for r in dict.fromkeys(repos) if _REPO.fullmatch(r)]
    if not repos:
        return None
    stale = data.get("stale_days")
    stale_days = clamp(int(stale), MIN_STALE_DAYS, MAX_STALE_DAYS) if isinstance(stale, int) else None
    drafts = data.get("include_drafts") if isinstance(data.get("include_drafts"), bool) else None
    return Parsed(tuple(repos[:MAX_REPOS]), max(0, len(repos) - MAX_REPOS), stale_days, drafts, None,
                  "json" in str(data.get("output") or "").casefold(), True)


@dataclass(frozen=True)
class Followup:
    kind: str = ""          # nudge / nudge_all / why / load / refresh / queue / approval_word / "" (unknown)
    number: int = 0
    only: str = ""


def followup(text: str, known_repos: tuple[str, ...]) -> Followup:
    t = (text or "").strip()
    if _APPROVAL_WORDS.match(t):
        return Followup("approval_word")
    if _NUDGE_ALL.search(t):
        return Followup("nudge_all")
    m = _NUDGE_N.search(t)
    if m:
        return Followup("nudge", int(m.group(1)))
    if _NUDGE_TOP.search(t):
        return Followup("nudge", 1)
    m = _WHY.search(t)
    if m:
        return Followup("why", int(m.group(1) or m.group(2)))
    only = _only(t, known_repos)
    if only:
        return Followup("queue", only=only)
    if _LOAD.search(t):
        return Followup("load")
    if _REFRESH.search(t):
        return Followup("refresh")
    if _SHOW.search(t):
        return Followup("queue")
    return Followup()


def _only(text: str, known: tuple[str, ...]) -> str:
    m = _ONLY.search(text)
    if not m:
        return ""
    wanted = m.group(1).casefold()
    return next((r for r in known if r.casefold() == wanted or r.split("/", 1)[1].casefold() == wanted), "")


def shows_all(text: str) -> bool:
    return bool(re.search(r"\b(?:all repos|every repo|all repositories|show all)\b", text or "", re.IGNORECASE))
