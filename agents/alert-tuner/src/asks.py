"""Pure: what the user asked for, read in code: the window, the firing threshold, a service, the
repository, which proposals to open a pull request for, which alert's incidents to show, structured
output, and an envelope from a previous workflow step. The model only decides whether a request is in
scope at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from noise import Params

DEFAULT_DAYS, MIN_DAYS, MAX_DAYS = 30, 7, 90
DEFAULT_N, MIN_N, MAX_N = 5, 2, 50
MAX_PR_ALERTS = 3

_REPO = re.compile(r"(?<![\w./-])([A-Za-z0-9][A-Za-z0-9_.-]{0,38})/([A-Za-z0-9_.-]{1,100})(?![\w/-])")
_DAYS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s*(day|days|d|week|weeks|w)\b|\b(\d{1,3})\s*(?:d|days)\b",
                   re.IGNORECASE)
_N = re.compile(r"\bn\s*=\s*(\d{1,3})\b|\b(\d{1,3})\s*\+\s*times\b|\b(?:at least|min(?:imum)?)\s+(\d{1,3})\s*(?:times|firings)?"
                r"|\b(\d{1,3})\s+or\s+more\s+times\b|\bfired\s+(\d{1,3})\+?", re.IGNORECASE)
_SERVICE = re.compile(r"\b(?:for|only|in|of|on)\s+(?:the\s+)?service\s+[\"'`]?([A-Za-z0-9][\w.-]{0,60})"
                      r"|\bservice\s*[:=]\s*[\"'`]?([A-Za-z0-9][\w.-]{0,60})|\bonly\s+([a-z0-9][\w.-]*-[\w.-]+)\b",
                      re.IGNORECASE)
_PR = re.compile(r"\b(?:open|create|raise|make|file|send|do)\b.{0,40}?\b(?:pr|prs|pull\s+requests?)\b", re.IGNORECASE)
_PR_NUMBERS = re.compile(r"#\s*(\d{1,2})\b")
_ORDINALS = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}
_SHOW = re.compile(r"\b(?:show|list|see|give me)\b.{0,20}?\bincidents?\b\s+(?:for|of|from)\s+[\"'`]?(.+?)[\"'`]?\s*[?.!]*$",
                   re.IGNORECASE)
_ACK_ONLY = re.compile(r"\binclud\w*\s+(?:the\s+)?ack(?:nowledged)?(?:[- ]only)?\b", re.IGNORECASE)
_JSON = re.compile(r"\b(?:as|in)\s+json\b|\bjson\s+only\b", re.IGNORECASE)
_PATH_HINT = re.compile(r"(?<![\w/])((?:[\w.-]+/)+)(?=\s|$|[,.;])")


@dataclass(frozen=True)
class Ask:
    kind: str                        # analyze | pr | show
    params: Params
    repo: str = ""
    path_hint: str = ""
    numbers: tuple[int, ...] = ()
    show: str = ""
    wants_json: bool = False
    capped: bool = False             # more than MAX_PR_ALERTS proposals were named


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def repo_in(text: str) -> str:
    for m in _REPO.finditer(text or ""):
        owner, name = m.group(1), m.group(2).rstrip(".")
        if not owner.isdigit() and "." not in owner and name:
            return f"{owner}/{name}"
    return ""


def window_days(text: str) -> int | None:
    m = _DAYS.search(text or "")
    if not m:
        return None
    if m.group(3):
        return clamp(int(m.group(3)), MIN_DAYS, MAX_DAYS)
    weeks = m.group(2).casefold().startswith("w")
    return clamp(int(m.group(1)) * (7 if weeks else 1), MIN_DAYS, MAX_DAYS)


def min_firings(text: str) -> int | None:
    m = _N.search(text or "")
    if not m:
        return None
    return clamp(int(next(g for g in m.groups() if g)), MIN_N, MAX_N)


def service(text: str) -> str:
    m = _SERVICE.search(text or "")
    return (m.group(1) or m.group(2) or m.group(3) or "").rstrip(".,") if m else ""


def pr_numbers(text: str) -> tuple[tuple[int, ...], bool]:
    found = [int(n) for n in _PR_NUMBERS.findall(text or "")]
    found += [v for k, v in _ORDINALS.items() if re.search(rf"\b{k}\b", text or "", re.IGNORECASE)]
    unique = tuple(dict.fromkeys(n for n in found if n > 0)) or (1,)
    return unique[:MAX_PR_ALERTS], len(unique) > MAX_PR_ALERTS


def envelope_params(text: str) -> dict:
    """Fields a previous step's envelope carries: window_days, min_firings, repo."""
    body = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not body.startswith("{"):
        return {}
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in ("window_days", "min_firings", "repo", "service") if data.get(k) not in (None, "")}


def read(text: str, previous: Params | None = None, remembered_repo: str = "") -> Ask:
    base = previous or Params(DEFAULT_DAYS, DEFAULT_N)
    env = envelope_params(text)
    days = _int(env.get("window_days"), MIN_DAYS, MAX_DAYS) or window_days(text) or base.window_days
    n = _int(env.get("min_firings"), MIN_N, MAX_N) or min_firings(text) or base.min_firings
    svc = str(env.get("service") or "") or service(text) or (base.service if previous else "")
    ack = True if _ACK_ONLY.search(text or "") else base.include_ack_only
    params = replace(base, window_days=days, min_firings=n, service=svc, include_ack_only=ack)
    repo = repo_in(str(env.get("repo") or "")) or repo_in(text) or remembered_repo
    hint = next((h for h in _PATH_HINT.findall(text or "") if not repo or not repo.startswith(h.rstrip("/"))), "")
    wants_json = bool(_JSON.search(text or ""))
    if _PR.search(text or ""):
        numbers, capped = pr_numbers(text)
        return Ask("pr", params, repo, hint, numbers, wants_json=wants_json, capped=capped)
    shown = _SHOW.search((text or "").strip())
    if shown:
        return Ask("show", params, repo, show=shown.group(1).strip(), wants_json=wants_json)
    return Ask("analyze", params, repo, hint, wants_json=wants_json)


def changes_params(text: str) -> bool:
    """The message only adjusts the last analysis ("N = 3", "only service payments", "last 14 days")."""
    return bool(min_firings(text) or window_days(text) or service(text) or _ACK_ONLY.search(text or ""))


def _int(value: object, low: int, high: int) -> int | None:
    try:
        return clamp(int(str(value)), low, high) if value not in (None, "") else None
    except ValueError:
        return None


def wants_json(text: str) -> bool:
    """"as json" in the message: the reply's text part is the envelope as a fenced json block."""
    return bool(_JSON.search(text or ""))
