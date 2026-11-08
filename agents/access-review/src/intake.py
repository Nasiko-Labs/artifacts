"""Pure: what one message holds. Labelled pastes (`access:`, `roster:`, `policy:`), unlabelled tables
classified by their header columns, an envelope from a previous workflow step, the code-host
organization and repositories named, JSON mode, and the follow-up commands code recognises itself."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import parse

MAX_PASTE_CHARS = 100_000
MAX_NAMED_REPOS = 10
_LABEL = re.compile(r"^\s*(access|access export|export|roster|org chart|policy)\s*:\s*(.*)$", re.IGNORECASE)
_LABEL_KIND = {"access": "access", "access export": "access", "export": "access", "roster": "roster",
               "org chart": "roster", "policy": "policy"}
_ORG = re.compile(r"\b(?:org|organi[sz]ation)\s*[:=]?\s+@?([A-Za-z0-9][A-Za-z0-9-]{0,38})\b", re.IGNORECASE)
_ORG_STOP = frozenset({"access", "chart", "members", "member", "membership", "owners", "owner", "admins", "admin",
                       "level", "wide", "roster", "list", "data", "the", "a", "an", "my", "our", "your", "review",
                       "and", "name", "is", "too", "as", "with", "for", "to", "of", "in", "on", "units", "unit"})
_WANTS_CODE_HOST = re.compile(r"\bcode[- ]host\b|\boutside collaborators?\b|\brepo(?:sitory)? admins?\b|"
                              r"\b(?:org|organi[sz]ation)\b(?!\s+(?:chart|roster|list|units?|structure))", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w./-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)(?![\w/-])")
_JSON_MODE = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\boutput json\b", re.IGNORECASE)
_MAP = re.compile(r"\bmap\s+@?([A-Za-z0-9][A-Za-z0-9-]{0,38})\s+(?:to|=|->)\s+([\w.+'-]+@[\w-]+(?:\.[\w-]+)+)",
                  re.IGNORECASE)
_REMOVE = re.compile(r"\b(?:remove|revoke|disable|suspend|deactivate|delete|deprovision|offboard|kick)\b",
                     re.IGNORECASE)
_DRAFT_WORDS = re.compile(r"\bdraft|\brequests?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    access: tuple[str, ...] = ()      # pasted access tables (each a block of lines)
    roster: tuple[str, ...] = ()
    policy_text: str = ""
    unclassified: tuple[str, ...] = ()  # tables whose headers fit neither kind
    org: str = ""
    repos: tuple[str, ...] = ()
    wants_code_host: bool = False
    envelope_access: tuple = ()
    envelope_roster: tuple = ()
    prose: str = ""                   # the message minus the pasted tables

    @property
    def has_data(self) -> bool:
        return bool(self.access or self.roster or self.unclassified or self.envelope_access or self.envelope_roster)


def wants_json(text: str) -> bool:
    return bool(_JSON_MODE.search(text or ""))


def read(text: str) -> Request:
    text = (text or "")[:MAX_PASTE_CHARS]
    env = _envelope(text)
    if env is not None:
        org = str(env.get("org") or "").strip()
        return Request(org=org if _ORG_NAME.fullmatch(org) else "", wants_code_host=bool(org),
                       envelope_access=parse.records_of(_list(env.get("access"))),
                       envelope_roster=parse.records_of(_list(env.get("roster"))))
    blocks, prose = _blocks(text)
    kinds: dict[str, tuple[str, ...]] = {"access": (), "roster": (), "policy": (), "unclassified": ()}
    for kind, body in blocks:
        kind = kind or _classify(body)
        kinds = {**kinds, kind: (*kinds[kind], body)}
    org = org_in(prose)
    return Request(kinds["access"], kinds["roster"], "\n".join(kinds["policy"]), kinds["unclassified"], org,
                   repos_in(prose, org), bool(org) or bool(_WANTS_CODE_HOST.search(prose)), prose=prose)


_ORG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")


def org_in(text: str) -> str:
    for m in _ORG.finditer(text or ""):
        if m.group(1).casefold() not in _ORG_STOP:
            return m.group(1)
    return ""


def repos_in(text: str, org: str) -> tuple[str, ...]:
    found = [f"{a}/{b}" for a, b in _REPO.findall(text or "") if org and a.casefold() == org.casefold()]
    return tuple(dict.fromkeys(found))[:MAX_NAMED_REPOS]


def classify_header(header: str) -> str:
    """access | roster | unclassified, from the canonical columns a header line maps to."""
    cols = {parse.canonical(c) for c in re.split(r"[\t|;,]", header)} - {None}
    access, roster = len(cols & parse.ACCESS_SIGNALS), len(cols & parse.ROSTER_SIGNALS)
    if access > roster:
        return "access"
    if roster > access:
        return "roster"
    return "unclassified"


def _classify(body: str) -> str:
    header = next((ln for ln in body.splitlines() if parse.looks_like_header(ln)), "")
    return classify_header(header) if header else "unclassified"


def _blocks(text: str) -> tuple[list[tuple[str, str]], str]:
    """(kind or "", block text) per paste, and the prose left over. A labelled block runs to the next
    label; an unlabelled table is a header line plus the delimited lines under it."""
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    prose: list[str] = []
    i = 0
    while i < len(lines):
        m = _LABEL.match(lines[i])
        if m:
            j = i + 1
            while j < len(lines) and not _LABEL.match(lines[j]):
                j += 1
            kind = _LABEL_KIND[m.group(1).casefold()]
            body = [m.group(2), *lines[i + 1:j]] if m.group(2).strip() else lines[i + 1:j]
            taken, rest = _split_table(body) if kind != "policy" else (body, [])
            blocks.append((kind, "\n".join(taken)))
            prose.extend(rest)
            i = j
        elif parse.looks_like_header(lines[i]):
            taken, rest = _split_table(lines[i:])
            blocks.append(("", "\n".join(taken)))
            i += len(taken)
        else:
            prose.append(lines[i])
            i += 1
    return blocks, "\n".join(prose)


def _split_table(lines: Sequence[str]) -> tuple[list[str], list[str]]:
    """The table lines (header + delimited rows) and the lines after it."""
    start = next((k for k, ln in enumerate(lines) if parse.looks_like_header(ln)), None)
    if start is None:
        return list(lines), []
    delim = parse.sniff(lines[start])
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or delim in lines[end]):
        end += 1
    return list(lines[:end]), list(lines[end:])


def _envelope(text: str) -> Mapping | None:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, Mapping) and any(k in data for k in ("access", "roster", "org")):
        return data
    return None


def _list(value) -> list:
    return value if isinstance(value, list) else []


# --- follow-up commands (on a stored review) -------------------------------------------------------

@dataclass(frozen=True)
class Command:
    kind: str = ""          # show | drafts | remove | map | policy | "" (not recognised)
    finding: str = ""       # a finding kind filter
    system: str = ""        # a system filter
    login: str = ""
    email: str = ""


_KIND_WORDS = (("terminated", "terminated_has_access"), ("not on roster", "not_on_roster"),
               ("not on the roster", "not_on_roster"), ("dormant", "dormant"),
               ("outside collaborator", "outside_collaborator_admin"), ("owners", "excess_owners"),
               ("without role", "privileged_without_role_need"), ("who has admin", "privileged"),
               ("privileged", "privileged"), ("manager", "no_manager_or_unknown_title"),
               ("service account", "service_account_review"), ("unmatched", "unmatched_identity"))


def command(text: str, systems: Sequence[str]) -> Command:
    lowered = (text or "").casefold()
    m = _MAP.search(text or "")
    if m:
        return Command("map", login=m.group(1).casefold(), email=m.group(2).casefold())
    finding = next((k for w, k in _KIND_WORDS if w in lowered), "")
    system = next((s for s in systems if s and re.search(rf"\bonly\s+{re.escape(s.casefold())}\b", lowered)), "")
    if _REMOVE.search(lowered) and not _DRAFT_WORDS.search(lowered):
        return Command("remove", finding, system)
    if "draft" in lowered or "removal request" in lowered:
        return Command("drafts", finding, system)
    if system or re.search(r"\b(?:show|list|who|which)\b", lowered) and finding:
        return Command("show", finding, system)
    return Command()
