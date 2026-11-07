"""Pure: the optional questions a user asks about the config, read in code from the words around it.

cron: a time zone, a start time, how many runs; regex: test strings and the flavour; nginx: request URLs
and a Host; IAM: one action and/or resource ("can it read arn:aws:s3:::logs/a.txt?")."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

MAX_TESTS = 10
MAX_URLS = 5

_TZ = re.compile(r"\b((?:Africa|America|Antarctica|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Za-z_]+"
                 r"(?:/[A-Za-z_]+)?|UTC)\b")
_START = re.compile(r"\b(?:after|from|starting)\s+(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2}))?")
_COUNT = re.compile(r"\bnext\s+(\d{1,2})\s+(?:runs?|times?|dates?)\b", re.IGNORECASE)
_TEST_CUE = re.compile(r"\b(?:test(?:s|\s+(?:it\s+)?(?:on|with|against))?|try(?:\s+it)?\s+on|examples?)\s*:\s*(.+)",
                       re.IGNORECASE | re.DOTALL)
_QUOTED = re.compile(r"\"([^\"\n]{1,200})\"")
_FLAVOURS = (("javascript", r"\b(?:javascript|js|typescript|node)\b"), ("python", r"\bpython\b"),
             ("pcre", r"\b(?:pcre|php|perl)\b"))
_URL = re.compile(r"https?://[^\s`'\"<>]+")
_PATH = re.compile(r"(?:(?<=\s)|(?<=`)|^)(/[A-Za-z0-9._~%!$&'()*+,;=:@/\-?]*)")
_HOST = re.compile(r"\bHost:\s*([A-Za-z0-9.\-]+)", re.IGNORECASE)
_ARN = re.compile(r"\barn:aws[a-z\-]*:[^\s`'\",)]+")
_ACTION = re.compile(r"\b([a-z0-9\-]{2,30}:[A-Za-z*]+)\b")
_VERBS = {"read": "GetObject", "get": "GetObject", "download": "GetObject", "write": "PutObject",
          "upload": "PutObject", "put": "PutObject", "delete": "DeleteObject", "list": "ListBucket"}


@dataclass(frozen=True)
class Probes:
    tz: str | None = None
    start: datetime | None = None
    count: int | None = None
    tests: tuple[str, ...] = ()
    flavour: str | None = None
    urls: tuple[str, ...] = ()
    host: str | None = None
    action: str | None = None
    resource: str | None = None
    words: str = ""


def read(words: str) -> Probes:
    """`words` is the user's text outside the config itself."""
    text = words or ""
    return Probes(tz=_tz(text), start=_start(text), count=_count(text), tests=tests(text), flavour=flavour(text),
                  urls=_urls(text), host=_host(text), action=_action(text), resource=_resource(text), words=text)


def _tz(text: str) -> str | None:
    match = _TZ.search(text)
    return match.group(1) if match else None


def _start(text: str) -> datetime | None:
    match = _START.search(text)
    if not match:
        return None
    hour, minute = int(match.group(2) or 0), int(match.group(3) or 0)
    try:
        return datetime.fromisoformat(match.group(1)).replace(hour=hour, minute=minute)
    except ValueError:
        return None


def _count(text: str) -> int | None:
    match = _COUNT.search(text)
    return int(match.group(1)) if match else None


def tests(text: str) -> tuple[str, ...]:
    found: list[str] = []
    cue = _TEST_CUE.search(text)
    if cue:
        body = cue.group(1).strip()
        pieces = body.splitlines() if "\n" in body else body.split(",")
        found += [p.strip().strip("`\"'").strip() for p in pieces]
    found += _QUOTED.findall(text)
    unique = list(dict.fromkeys(p for p in found if p))
    return tuple(unique[:MAX_TESTS])


def flavour(text: str) -> str | None:
    for name, pattern in _FLAVOURS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


def _urls(text: str) -> tuple[str, ...]:
    found = [u.rstrip(".,;:?)") for u in _URL.findall(text)]
    rest = _URL.sub(" ", text)
    found += [p.rstrip(".,;:?)") for p in _PATH.findall(rest)]
    return tuple(dict.fromkeys(u for u in found if u))[:MAX_URLS]


def _host(text: str) -> str | None:
    match = _HOST.search(text)
    return match.group(1) if match else None


def _resource(text: str) -> str | None:
    match = _ARN.search(text)
    return match.group(0).rstrip(".?") if match else None


def _action(text: str) -> str | None:
    no_arns = _ARN.sub(" ", text)
    for match in _ACTION.finditer(no_arns):
        if not match.group(1).lower().startswith(("http", "host")):
            return match.group(1)
    resource = _resource(text)
    if resource and resource.startswith("arn:aws:s3:::"):
        for verb, action in _VERBS.items():
            if re.search(rf"\b{verb}\b", no_arns, re.IGNORECASE):
                return f"s3:{action}"
    return None
