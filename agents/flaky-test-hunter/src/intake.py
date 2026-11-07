"""What the user's message asks for, read in code first: the repository, workflow, branch and run count,
an envelope from an earlier workflow step, the paste fallback, JSON output, and the follow-up commands
("show evidence for #2", "draft the PR for #1", "last 50 runs", "only workflow X", "skip ticket, PR only").
The model is asked only when code cannot tell, always with the scope rule and the off-topic sentinel."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from scan import Params

DEFAULT_RUNS, MIN_RUNS, MAX_RUNS = 30, 10, 50

_FILE_EXT = re.compile(r"\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|rb|md|txt|json|ya?ml|toml|cfg|ini|lock)$", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w./@-])([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_][\w.-]{0,99})(?![\w/-])")
_NOT_OWNERS = frozenset({"and", "or", "pr", "ci", "and/or", "n", "a"})
_RUNS = re.compile(r"\b(?:last|past|previous|over)\s+(\d{1,3})\s+(?:ci\s+|workflow\s+)?runs?\b", re.IGNORECASE)
_WORKFLOW = (re.compile(r"\bworkflow\s+[\"'`“]([^\"'`”]{1,80})[\"'`”]", re.IGNORECASE),
             re.compile(r"\bworkflow\s+([\w.-]{1,60})\b", re.IGNORECASE),
             re.compile(r"\b(?:on|in|for)\s+the\s+([\w.-]{1,60})\s+workflow\b", re.IGNORECASE))
_WORKFLOW_STOP = frozenset({"in", "on", "for", "over", "runs", "run", "the", "and"})
_BRANCH = re.compile(r"\bbranch\s+[`\"']?([\w./-]{1,100})", re.IGNORECASE)
_EVIDENCE = re.compile(r"\bevidence\b.*?#?\s*(\d{1,2})\b|\bshow\s+(?:me\s+)?#\s*(\d{1,2})\b", re.IGNORECASE)
_DRAFT = re.compile(r"\b(?:draft|open|make|create|prepare|file|quarantine)\b.*\b(?:pr|pull request|ticket|issue|skip)\b"
                    r"|\bskip\s+(?:it|test|#)|\bpr only\b", re.IGNORECASE)
_RANK = re.compile(r"#\s*(\d{1,2})\b|\b(?:number|rank|test)\s+(\d{1,2})\b", re.IGNORECASE)
_SKIP_TICKET = re.compile(r"\b(?:skip|no|without)\s+(?:the\s+)?(?:ticket|issue)\b|\bpr only\b", re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str           # evidence | draft | rescan | ""
    rank: int = 0
    skip_ticket: bool = False
    params: Params | None = None


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def repo_in(text: str) -> str:
    for m in _REPO.finditer(text or ""):
        owner, name = m.group(1), m.group(2).rstrip(".")
        if owner.casefold() in _NOT_OWNERS or _FILE_EXT.search(name) or name.casefold().endswith((".git",)):
            continue
        return f"{owner}/{name}"
    return ""


def runs_in(text: str) -> int:
    m = _RUNS.search(text or "")
    return max(MIN_RUNS, min(MAX_RUNS, int(m.group(1)))) if m else 0


def workflow_in(text: str) -> str:
    for pattern in _WORKFLOW:
        m = pattern.search(text or "")
        if m and m.group(1).strip().casefold() not in _WORKFLOW_STOP:
            return m.group(1).strip()
    return ""


def branch_in(text: str) -> str:
    m = _BRANCH.search(text or "")
    return m.group(1).rstrip(".,") if m else ""


def params_in(text: str, remembered: Params | None = None) -> Params:
    """Code-read parameters; anything the message leaves out comes from the remembered ones."""
    base = remembered or Params(runs=DEFAULT_RUNS)
    return Params(repo_in(text) or base.repo, workflow_in(text) or base.workflow, branch_in(text) or base.branch,
                  runs_in(text) or base.runs or DEFAULT_RUNS)


def envelope_params(text: str) -> Params | None:
    """An envelope from an earlier step: a JSON object (fenced or not) with a `repo`."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("repo"), str) or not repo_in(data["repo"]):
        return None
    runs = data.get("runs")
    count = max(MIN_RUNS, min(MAX_RUNS, int(runs))) if isinstance(runs, int) and not isinstance(runs, bool) else 30
    return Params(repo_in(data["repo"]), str(data.get("workflow") or ""), str(data.get("branch") or ""), count)


def command(text: str, remembered: Params) -> Command:
    """A follow-up recognised by code, or Command("") when the model has to read it."""
    m = _EVIDENCE.search(text or "")
    if m:
        return Command("evidence", int(m.group(1) or m.group(2)))
    if _DRAFT.search(text or ""):
        r = _RANK.search(text or "")
        return Command("draft", int(r.group(1) or r.group(2)) if r else 0, bool(_SKIP_TICKET.search(text)))
    runs, workflow = runs_in(text), workflow_in(text)
    if runs or workflow:
        return Command("rescan", params=replace(remembered, runs=runs or remembered.runs,
                                                workflow=workflow or remembered.workflow))
    return Command("")


def names_new_repo(text: str, remembered: str) -> bool:
    repo = repo_in(text)
    return bool(repo) and repo.casefold() != (remembered or "").casefold()
