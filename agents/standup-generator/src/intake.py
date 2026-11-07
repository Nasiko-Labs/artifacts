"""What the user asked for: people, repositories, tracker project, window.

Repositories (`owner/repo`, up to MAX_REPOS) and the window are parsed in code from the user's own
words. The one intake LLM call only names people and a project and flags off-topic requests; every
name it returns must appear in the message as whole words, else it is dropped. A previous step's
envelope (JSON with `repos`, `people`, `window`) is read directly, with no LLM call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import prompts
from checks import parse_answer
from deps import TurnDeps
from identity import clean_label, tokens
from llm import ask_once

MAX_REPOS = 5
MAX_NAMED = 15
_REPO = re.compile(r"(?<![\w./@:-])([A-Za-z0-9][\w.-]{0,38}/[A-Za-z0-9][\w.-]{0,99})(?![\w/-])")
_NOT_REPOS = frozenset({"and/or", "either/or", "w/o", "n/a", "i/o", "on/off", "yes/no"})
_EVERYONE = re.compile(r"\b(?:everyone|everybody|the (?:whole )?team|all of us|whole team)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    people: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()
    project: str = ""
    everyone: bool = False
    any_scope: bool = False  # the user chose to read whatever the tools return, with no repo or project

    @property
    def has_scope(self) -> bool:
        return bool(self.repos or self.project or self.any_scope)

    def to_dict(self) -> dict:
        return {"people": list(self.people), "repos": list(self.repos), "project": self.project,
                "everyone": self.everyone, "any_scope": self.any_scope}

    @staticmethod
    def from_dict(raw: Mapping) -> Request:
        return Request(tuple(raw.get("people", ())), tuple(raw.get("repos", ())), raw.get("project", ""),
                       bool(raw.get("everyone")), bool(raw.get("any_scope")))


@dataclass(frozen=True)
class Intake:
    status: str  # ok | off_topic
    request: Request = field(default_factory=Request)
    window_text: str = ""


def repos_in(text: str) -> tuple[str, ...]:
    found = [m.group(1).rstrip(".") for m in _REPO.finditer(text or "")]
    kept = [r for r in dict.fromkeys(found) if r.casefold() not in _NOT_REPOS and not r.split("/")[1].isdigit()]
    return tuple(kept[:MAX_REPOS])


def read_envelope(text: str) -> Intake | None:
    """A previous step's envelope: a JSON object with `repos`, `people` or `window`."""
    body = (text or "").strip()
    if not body.startswith(("{", "```")):
        return None
    parsed = parse_answer(body)
    if parsed is None or not any(k in parsed for k in ("repos", "people", "window")):
        return None
    repos = repos_in(" ".join(r for r in parsed.get("repos") or () if isinstance(r, str)))
    people = tuple(dict.fromkeys(_names(parsed.get("people"))))[:MAX_NAMED]
    window = parsed.get("window") if isinstance(parsed.get("window"), str) else ""
    project = parsed.get("project") if isinstance(parsed.get("project"), str) else ""
    return Intake("ok", Request(people=people, repos=repos, project=clean_label(project), everyone=not people),
                  window_text=window)


async def read_request(deps: TurnDeps, text: str) -> Intake:
    envelope = read_envelope(text)
    if envelope is not None:
        return envelope
    messages = ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                {"role": "user", "content": prompts.strip_markers(text)})
    answer = parse_answer(await ask_once(deps, messages)) or {}
    if answer.get("status") == "off_topic":
        return Intake("off_topic")
    people = named_in_message(answer.get("people") if isinstance(answer.get("people"), list) else (), text)
    project = answer.get("project") if isinstance(answer.get("project"), str) else ""
    project = clean_label(project) if project and _in_message(project, text) and "/" not in project else ""
    everyone = not people or answer.get("everyone") is True or bool(_EVERYONE.search(text))
    return Intake("ok", Request(people=people, repos=repos_in(text), project=project, everyone=everyone and not people),
                  window_text=text)


def with_scope_answer(request: Request, answer: str) -> Request:
    """The answer to the "Which repos or project?" question, merged into the paused request."""
    if answer.strip().casefold() == prompts.ANY_SCOPE.casefold():
        return replace(request, any_scope=True)
    repos = repos_in(answer)
    rest = _REPO.sub(" ", answer)
    project = clean_label(re.sub(r"\b(?:project|team|repos?|and|the|in|tracker)\b", " ", rest, flags=re.IGNORECASE))
    return replace(request, repos=repos or request.repos, project=project or request.project,
                   any_scope=not (repos or project))


def named_in_message(names, message: str) -> tuple[str, ...]:
    kept: list[str] = []
    for raw in names:
        if isinstance(raw, str) and _in_message(raw, message):
            label = clean_label(raw)
            if label and label.casefold() not in {k.casefold() for k in kept}:
                kept.append(label)
    return tuple(kept[:MAX_NAMED])


def _in_message(value: str, message: str) -> bool:
    wanted = tokens(value)
    return bool(wanted) and f" {' '.join(wanted)} " in f" {' '.join(tokens(message))} "


def _names(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        name = item.get("name") if isinstance(item, Mapping) else item
        if isinstance(name, str) and clean_label(name):
            out.append(clean_label(name))
    return out

