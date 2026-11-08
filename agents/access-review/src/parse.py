"""Pure: pasted tables (CSV, TSV, pipe or semicolon separated) and envelope records into typed rows.

Header names are matched against an alias table, dates in the usual admin-console formats are parsed,
and rows that cannot be read are counted, never guessed. Nothing here sees a model."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime

# Canonical column → header aliases (casefolded, punctuation-insensitive).
ALIASES: Mapping[str, tuple[str, ...]] = {
    "email": ("email", "mail", "e mail", "email address", "user email", "primary email", "user principal name",
              "userprincipalname", "upn", "login email", "work email"),
    "name": ("name", "full name", "display name", "displayname", "user", "user name", "username", "person",
             "employee", "employee name"),
    "role": ("role", "roles", "group", "groups", "permission", "permissions", "access level", "privilege",
             "admin role", "access"),
    "system": ("system", "app", "application", "service", "resource", "system app", "target", "account"),
    "last_sign_in": ("last sign in", "last signin", "last sign in time", "last login", "lastlogin", "last logon",
                     "last seen", "last activity", "last active", "last used", "last sign in date"),
    "status": ("status", "account status", "state", "employment status", "user status"),
    "created": ("created", "created at", "creation date", "created on", "date created", "created date"),
    "title": ("title", "job title", "position", "job"),
    "manager": ("manager", "reports to", "manager email", "manager name", "supervisor"),
    "department": ("department", "dept", "team", "org unit", "division"),
    "end_date": ("end date", "termination date", "terminated on", "last day", "exit date", "term date"),
}
ACCESS_SIGNALS = frozenset({"role", "system", "last_sign_in", "created"})
ROSTER_SIGNALS = frozenset({"title", "manager", "department", "end_date", "handle"})
_DELIMITERS = ("\t", "|", ";", ",")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_TERMINATED = ("terminated", "termed", "offboarded", "departed", "former", "left", "separated", "inactive")
_LEAVE = ("leave", "loa", "sabbatical")
_DISABLED = ("suspended", "disabled", "deprovisioned", "deleted", "blocked", "locked")
NEVER_WORDS = ("never", "never signed in", "none", "n/a", "-", "")


@dataclass(frozen=True)
class AccessRow:
    row: int                # 1-based row number in the user's paste (header excluded)
    email: str              # lowercased, "" when absent
    name: str
    system: str
    role: str
    last_sign_in: str       # ISO date, "" when never or unknown
    never_signed_in: bool
    created: str            # ISO date or ""
    disabled: bool


@dataclass(frozen=True)
class RosterRow:
    row: int
    email: str
    name: str
    title: str
    manager: str
    department: str
    status: str             # active | terminated | leave
    end_date: str           # ISO date or ""
    handle: str             # code-host login from a `git…` column, lowercased

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Table:
    columns: tuple[str | None, ...]   # canonical name per position (None = unknown column)
    rows: tuple[tuple[int, Mapping[str, str]], ...]
    rejected: int


def canonical(header: str) -> str | None:
    key = " ".join(_NON_WORD.sub(" ", header.casefold()).split())
    if key.startswith("git") or key in ("handle", "code host", "code host handle", "scm handle", "vcs login"):
        return "handle"
    return next((c for c, names in ALIASES.items() if key in names), None)


def sniff(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in _DELIMITERS}
    best = max(_DELIMITERS, key=lambda d: counts[d])
    return best if counts[best] else ","


def looks_like_header(line: str) -> bool:
    cells = _split(line, sniff(line))
    known = {canonical(c) for c in cells} - {None}
    return len(cells) >= 2 and len(known) >= 2 and bool(known & {"email", "name", "handle"})


def table(lines: Sequence[str]) -> Table:
    """The first header-looking line and the delimited rows under it; stops at the first non-blank line
    without the delimiter (prose after a table is not a row)."""
    body = [ln for ln in lines]
    start = next((i for i, ln in enumerate(body) if looks_like_header(ln)), None)
    if start is None:
        return Table((), (), 0)
    delim = sniff(body[start])
    columns = tuple(canonical(c) for c in _split(body[start], delim))
    rows: list[tuple[int, Mapping[str, str]]] = []
    rejected = 0
    for line in body[start + 1:]:
        if not line.strip():
            continue
        if delim not in line:
            break
        cells = _split(line, delim)
        if len(cells) > len(columns) and any(cells[len(columns):]) or len(cells) < 2:
            rejected += 1  # more filled cells than columns (an unquoted delimiter), or not a row at all
            continue
        cells = [*cells, *([""] * (len(columns) - len(cells)))]  # editors drop trailing empty cells
        rows.append((len(rows) + rejected + 1, {c: v for c, v in zip(columns, cells, strict=False) if c}))
    return Table(columns, tuple(rows), rejected)


def _split(line: str, delim: str) -> list[str]:
    text = line.strip(" \r\n")
    text = text.strip("|") if delim == "|" else text
    try:
        cells = next(csv.reader(io.StringIO(text), delimiter=delim, skipinitialspace=True))
    except (csv.Error, StopIteration):
        cells = text.split(delim)
    return [c.strip() for c in cells]


def access_rows(t: Table, records: Iterable[tuple[int, Mapping[str, str]]] = ()) -> tuple[tuple[AccessRow, ...], int]:
    out, rejected = [], t.rejected
    for number, cells in (*t.rows, *records):
        email = _email(cells.get("email", "")) or _email(cells.get("name", ""))
        name = "" if "@" in cells.get("name", "") else cells.get("name", "").strip()
        if not email and not name:
            rejected += 1
            continue
        last_raw = cells.get("last_sign_in", "").strip()
        last = parse_date(last_raw)
        status = cells.get("status", "").casefold()
        out.append(AccessRow(number, email, name, cells.get("system", "").strip(), cells.get("role", "").strip(),
                             last.isoformat() if last else "", last_raw.casefold() in NEVER_WORDS,
                             _iso(cells.get("created", "")), any(w in status for w in _DISABLED)))
    return tuple(out), rejected


def roster_rows(t: Table, records: Iterable[tuple[int, Mapping[str, str]]] = ()) -> tuple[tuple[RosterRow, ...], int]:
    out, rejected = [], t.rejected
    for number, cells in (*t.rows, *records):
        email = _email(cells.get("email", ""))
        name = cells.get("name", "").strip()
        if not email and not name:
            rejected += 1
            continue
        out.append(RosterRow(number, email, name, cells.get("title", "").strip(), cells.get("manager", "").strip(),
                             cells.get("department", "").strip(), status_word(cells.get("status", "")),
                             _iso(cells.get("end_date", "")), cells.get("handle", "").strip().lstrip("@").casefold()))
    return tuple(out), rejected


def records_of(items: Sequence) -> tuple[tuple[int, Mapping[str, str]], ...]:
    """Envelope records (`[{...}, ...]`) as rows under canonical keys; non-objects are skipped."""
    out = []
    for i, item in enumerate(items, start=1):
        if isinstance(item, Mapping):
            cells = {canonical(str(k)): str(v) for k, v in item.items() if v is not None and canonical(str(k))}
            out.append((i, cells))
    return tuple(out)


def status_word(raw: str) -> str:
    text = raw.casefold()
    if any(re.search(rf"\b{w}\b", text) for w in _TERMINATED):
        return "terminated"
    if any(re.search(rf"\b{w}\b", text) for w in _LEAVE):
        return "leave"
    return "active"


def parse_date(raw: str) -> date | None:
    text = (raw or "").strip()
    if text.casefold() in NEVER_WORDS:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return _date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return _date(int(m[3]), int(m[1]), int(m[2]))
    m = re.match(r"(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ -,]+(\d{4})", text)
    if m and m[2].casefold() in _MONTHS:
        return _date(int(m[3]), _MONTHS[m[2].casefold()], int(m[1]))
    m = re.match(r"([A-Za-z]{3})[a-z]* (\d{1,2}),? (\d{4})", text)
    if m and m[1].casefold() in _MONTHS:
        return _date(int(m[3]), _MONTHS[m[1].casefold()], int(m[2]))
    return None


def _date(y: int, mo: int, d: int) -> date | None:
    try:
        return datetime(y, mo, d).date()
    except ValueError:
        return None


def _iso(raw: str) -> str:
    d = parse_date(raw)
    return d.isoformat() if d else ""


def _email(raw: str) -> str:
    m = re.search(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", raw or "")
    return m.group(0).casefold() if m else ""
