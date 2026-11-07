"""Pure: what the user's message asks for, parsed in code: the repository, the new hire, the ticket (key,
#number, URL or pasted text), role words, JSON output, an envelope as input, and the follow-ups that
need no tool call. The model's intake answer only fills a gap, and only with words the user wrote."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from verify import parse_object

MAX_PASTED_TICKET_CHARS = 6_000
_JSON_WORDS = re.compile(r"\b(?:as json|json only|in json|json output)\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>()\"']+")
_REPO_TOKEN = re.compile(r"(?<![\w/.@-])([A-Za-z0-9][\w.-]{0,38})/([A-Za-z0-9_.-]{1,100})(?![\w/])")
_REPO_CUE = re.compile(r"\b(?:in|of|for|repo|repository|through|on|about):?\s+$", re.IGNORECASE)
_PATH_ROOTS = frozenset({"src", "lib", "app", "apps", "services", "service", "docs", "test", "tests", "pkg", "cmd",
                         "internal", "packages", "scripts", ".github", "web", "api", "server", "client", "config"})
_TICKET_KEY = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_TICKET_NUMBER = re.compile(r"(?<![\w&])#(\d{1,7})\b")
_HIRE = re.compile(r"\b(?:for|welcome)\s+([A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+)?)\b(?!/)")
_NOT_NAMES = frozenset({"The", "A", "An", "My", "Our", "New", "This", "That", "Me", "Us", "Them"})
_ROLE = re.compile(r"\b(backend|back-end|frontend|front-end|full-?stack|infra(?:structure)?|devops|mobile|data|"
                   r"platform|sre|ml)\b", re.IGNORECASE)
_FENCED = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_TICKET_LABEL = re.compile(r"\b(?:first\s+)?ticket\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)

_COMMANDS_ONLY = re.compile(r"(?:give me |show me |just |only )?(?:the )?(?:run )?commands?(?: only)?|"
                            r"(?:just|only) (?:the )?commands", re.IGNORECASE)
_SHORTER = re.compile(r"(?:make it |a )?(?:shorter|more concise|tl;?dr|briefer|condense it|summari[sz]e it)(?: please)?",
                      re.IGNORECASE)
_FULL = re.compile(r"(?:show |give me )?(?:the )?full guide(?: again)?|show (?:me )?the guide(?: again)?",
                   re.IGNORECASE)
_OWNERS = re.compile(r"(?:who (?:owns|maintains)|who should i ask about|who do i ask about|owners? of|who to ask about)"
                     r"\s+(?:the\s+)?`?(?P<path>[\w./*-]+)`?(?:\s+(?:directory|folder|dir|module|code))?", re.IGNORECASE)
_FOCUS = re.compile(r"focus (?:on|the guide on)\s+`?(?P<path>[\w./*-]+)`?", re.IGNORECASE)
_ADD_SECTION = re.compile(r"add (?:a |another )?section (?:on|about|for|covering)\s+(?P<topic>[\w ./-]{2,40}?)",
                          re.IGNORECASE)
_END = re.compile(r"[\s?.!]+$")


@dataclass(frozen=True)
class Request:
    repo: str = ""
    hire: str = ""
    ticket: str = ""            # key, "#12" or URL
    ticket_text: str = ""       # pasted ticket text
    role: str = ""
    owners_path: str = ""
    readme_text: str = ""       # pasted README (no code host)
    as_json: bool = False


@dataclass(frozen=True)
class FollowUp:
    kind: str                   # commands | shorter | full | owners | focus | section
    arg: str = ""


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def parse_request(text: str) -> Request:
    envelope = _envelope(text)
    if envelope is not None:
        return _from_envelope(envelope, wants_json(text))
    clean = _JSON_WORDS.sub("", text or "").strip()
    fenced = _FENCED.findall(clean)
    outside = _FENCED.sub(" ", clean)
    repo = find_repo(outside)
    ticket, ticket_text = _ticket(outside, repo, fenced)
    hire = next((m for m in _HIRE.findall(outside) if m.split()[0] not in _NOT_NAMES), "")
    role = _ROLE.search(outside)
    owners = _OWNERS.search(_END.sub("", outside))
    readme = fenced[0].strip() if fenced and not ticket_text and _looks_like_readme(fenced[0]) else ""
    return Request(repo=repo, hire=hire, ticket=ticket, ticket_text=ticket_text,
                   role=role.group(1).casefold() if role else "",
                   owners_path=owners.group("path").strip("/.") if owners else "",
                   readme_text=readme, as_json=wants_json(text))


def find_repo(text: str) -> str:
    """The `owner/repo` the user named: one after "in/of/for/repo ..." first, else the first that is not a path."""
    candidates = []
    for match in _REPO_TOKEN.finditer(_URL.sub(" ", text or "")):
        owner, name = match.group(1), match.group(2).rstrip(".")
        if owner.casefold() in _PATH_ROOTS or "." in name and name.rsplit(".", 1)[-1] in ("py", "js", "ts", "md", "go"):
            continue
        cued = bool(_REPO_CUE.search(text[:match.start()][-20:]))
        candidates.append((not cued, match.start(), f"{owner}/{name}"))
    return min(candidates)[2] if candidates else ""


def _ticket(text: str, repo: str, fenced: list[str]) -> tuple[str, str]:
    key = _TICKET_KEY.search(text)
    if key:
        return key.group(1), ""
    number = _TICKET_NUMBER.search(text)
    if number:
        return f"#{number.group(1)}", ""
    url = next((u.rstrip(".,") for u in _URL.findall(text) if repo.casefold() not in u.casefold() or "/issues/" in u), "")
    if url:
        return url, ""
    labelled = _TICKET_LABEL.search(text)
    if labelled and len(labelled.group(1).strip()) > 20:
        return "", labelled.group(1).strip()[:MAX_PASTED_TICKET_CHARS]
    if fenced and not _looks_like_readme(fenced[0]):
        return "", fenced[0].strip()[:MAX_PASTED_TICKET_CHARS]
    return "", ""


def _looks_like_readme(block: str) -> bool:
    return bool(re.search(r"^\s{0,3}#{1,3}\s", block, re.MULTILINE))


def merge_model(req: Request, answer: str, text: str) -> tuple[Request, bool]:
    """(request with gaps filled from the model's intake JSON, off_topic). A model value is taken only
    when the user wrote it verbatim."""
    data = parse_object(answer) or {}
    if str(data.get("status") or "").casefold() == "off_topic":
        return req, True
    lowered = (text or "").casefold()

    def said(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) and value.strip() and value.strip().casefold() in lowered else ""

    repo = req.repo or (said("repo") if _REPO_TOKEN.fullmatch(said("repo")) else "")
    return replace(req, repo=repo, hire=req.hire or said("hire"),
                   ticket=req.ticket or ("" if req.ticket_text else said("ticket")),
                   role=req.role or said("role").casefold()), False


def follow_up(text: str) -> FollowUp | None:
    """A follow-up that the stored guide answers in code."""
    plain = _END.sub("", (text or "").strip())
    if _COMMANDS_ONLY.fullmatch(plain):
        return FollowUp("commands")
    if _SHORTER.fullmatch(plain):
        return FollowUp("shorter")
    if _FULL.fullmatch(plain):
        return FollowUp("full")
    for kind, pattern, group in (("owners", _OWNERS, "path"), ("focus", _FOCUS, "path"),
                                 ("section", _ADD_SECTION, "topic")):
        match = pattern.fullmatch(plain)
        if match:
            return FollowUp(kind, match.group(group).strip().strip("/"))
    return None


def _envelope(text: str) -> dict[str, Any] | None:
    stripped = _JSON_WORDS.sub("", text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    return data if isinstance(data, dict) and any(k in data for k in ("repo", "hire", "ticket")) else None


def _from_envelope(data: dict[str, Any], as_json: bool) -> Request:
    def text(key: str) -> str:
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("key") or value.get("title")
        return value.strip() if isinstance(value, str) else ""

    repo = text("repo")
    return Request(repo=repo if _REPO_TOKEN.fullmatch(repo) else "", hire=text("hire"), ticket=text("ticket"),
                   as_json=as_json)
