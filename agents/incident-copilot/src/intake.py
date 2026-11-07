"""Intake: which incident to investigate, parsed in code first. Only when code finds neither an incident
reference nor a service does one tool-less LLM call run; its values are kept only if they appear in the
user's message, and its start-time words are parsed here, never by the model."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import prompts
from deps import TurnDeps
from jsonutil import load_json_object, text_of
from loops import ask_once
from windows import parse_lookback, parse_start

_URL = re.compile(r"https?://[^\s<>\"')]+")
_INCIDENT_WORD = re.compile(r"\bincident\s+(?:id\s+)?#?([A-Za-z0-9][A-Za-z0-9_-]{2,})\b", re.IGNORECASE)
_INC_ID = re.compile(r"(?<![#\w-])(INC-?\d+)\b", re.IGNORECASE)
_HASH_ID = re.compile(r"(?<![\w/])#(\d{3,})\b")
_QUOTED = re.compile(r"[\"'`]([a-z0-9][a-z0-9_.-]{1,80})[\"'`]", re.IGNORECASE)
_AFTER = re.compile(r"\b(?:the|for|service)\s+([a-z0-9][a-z0-9_.-]{1,80})(?:\s+([a-z]+))?", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w/.@-])([A-Za-z][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9])(?![\w/])")
_CHANNEL = re.compile(r"(?<![\w/&])#([a-z0-9][a-z0-9_-]{0,79})\b", re.IGNORECASE)
_NOT_REPOS = frozenset({"and/or", "he/she", "his/her", "n/a", "w/o", "either/or"})
_SERVICE_NOUNS = frozenset({"service", "api", "alert", "alerts", "incident", "outage", "errors", "app", "job",
                            "worker", "page", "pages", "deploy", "cluster", "database", "queue", "is", "was"})
_NOT_SERVICES = frozenset({"the", "a", "an", "last", "past", "incident", "alert", "alerts", "service", "same",
                           "latest", "current", "top", "suspect", "card", "repo", "author", "channel", "team",
                           "this", "that", "it", "me", "us", "page", "outage", "errors", "on-call", "oncall"})


@dataclass(frozen=True)
class Target:
    incident_ref: str = ""
    service: str = ""
    repo: str = ""
    start: datetime | None = None
    lookback: timedelta | None = None
    channel: str = ""

    @property
    def found(self) -> bool:
        return bool(self.incident_ref or self.service)


def _strip(value: str) -> str:
    return value.rstrip(".,;:!?)")


def _incident_ref(text: str, urls: list[str]) -> str:
    url = next((u for u in urls if "incident" in u.casefold()), "")
    if url:
        return url
    for pattern in (_INC_ID, _INCIDENT_WORD, _HASH_ID):
        for match in pattern.finditer(text):
            if any(ch.isdigit() for ch in match.group(1)):
                return match.group(1)
    return ""


def _service(text: str) -> str:
    quoted = _QUOTED.search(text)
    if quoted:
        return quoted.group(1).lower()
    for match in _AFTER.finditer(text):
        name, after = _strip(match.group(1)).lower(), (match.group(2) or "").lower()
        if name in _NOT_SERVICES or name.isdigit() or len(name) < 3:
            continue
        if re.search(r"[-_.\d]", name) or after in _SERVICE_NOUNS:
            return name
    return ""


def parse(text: str, now: datetime) -> Target:
    message = text or ""
    urls = [_strip(u) for u in _URL.findall(message)]
    without_urls = _URL.sub(" ", message)
    repo = next((_strip(m.group(1)) for m in _REPO.finditer(without_urls)
                 if m.group(1).casefold() not in _NOT_REPOS), "")
    service_text = _REPO.sub(" ", without_urls)
    channel = next((m.group(1) for m in _CHANNEL.finditer(without_urls) if not m.group(1).isdigit()), "")
    return Target(incident_ref=_incident_ref(without_urls, urls), service=_service(service_text), repo=repo,
                  start=parse_start(message, now), lookback=parse_lookback(message),
                  channel=f"#{channel}" if channel else "")


@dataclass(frozen=True)
class IntakeResult:
    target: Target
    status: str          # "ok" | "off_topic" | "need_target"


async def intake(deps: TurnDeps, text: str) -> IntakeResult:
    target = parse(text, deps.now)
    if target.found:
        return IntakeResult(target, "ok")
    answer = await ask_once(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                   {"role": "user", "content": text}))
    data = load_json_object(answer) or {}
    status = text_of(data.get("status"))
    if status == "off_topic":
        return IntakeResult(target, "off_topic")
    folded = text.casefold()

    def stated(key: str) -> str:
        value = text_of(data.get(key))
        return value if value and value.casefold() in folded else ""

    start_text = stated("start_text")
    filled = replace(target, service=stated("service"), incident_ref=stated("incident_ref"),
                     start=target.start or (parse_start(start_text, deps.now) if start_text else None))
    return IntakeResult(filled, "ok" if filled.found else "need_target")
