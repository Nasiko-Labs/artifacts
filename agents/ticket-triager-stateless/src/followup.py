"""Pure: follow-ups on the stored triage, answered with zero model calls: filters, sorting, top N, "why is
#X P2?", as json / as csv, show all, and "add these: <tickets>"."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from model import Ticket
from rules import sort_key

MAX_COMMAND_WORDS = 20
MAX_WHY_MATCHES = 3

_ADD = re.compile(r"^\s*(?:also\s+)?add (?:these|this|more|the following)\b\s*(?:tickets?)?\s*:?\s*", re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b|\bexplain\b|\bhow come\b", re.IGNORECASE)
_CSV = re.compile(r"\bcsv\b", re.IGNORECASE)
_RESET = re.compile(r"\bshow all\b|\ball tickets\b|\breset\b|\bclear (?:the )?filters?\b|\bfull (?:list|table)\b",
                    re.IGNORECASE)
_PRIORITY = re.compile(r"\bP([1-4])\b", re.IGNORECASE)
_RISK = re.compile(r"\b(high|medium|low)(?:[ -]escalation)?[ -]risk\b", re.IGNORECASE)
_TIER = re.compile(r"\b(enterprise|business|pro|free)\b", re.IGNORECASE)
_OPEN = re.compile(r"\bopen (?:ones|tickets)?\s*only\b|\bonly (?:the )?open\b", re.IGNORECASE)
_TOP = re.compile(r"\btop (\d{1,3})\b", re.IGNORECASE)
_SORT = re.compile(r"\bsort(?:ed)? by (risk|age|priority)\b|\b(oldest) first\b|\b(riskiest) first\b", re.IGNORECASE)
_JSON = re.compile(r"\bjson\b", re.IGNORECASE)
_ID = re.compile(r"#?\b([A-Za-z]*-?\d[\w-]*)\b")
_WORD = re.compile(r"[a-z][a-z_-]{3,}")
_STOP = frozenset(["why", "explain", "come", "this", "that", "ticket", "tickets", "high", "medium", "risk", "priority", "what", "about", "with", "from", "have", "does", "is", "the", "was", "were", "their", "there", "they", "them", "which", "marked", "mark", "rated", "rate", "escalation", "show", "one", "customer", "customers"])


@dataclass(frozen=True)
class View:
    priorities: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()
    open_only: bool = False
    top: int | None = None
    sort: str = "priority"

    def note(self) -> str:
        bits = [*(f"{p}" for p in self.priorities), *(f"{r} risk" for r in self.risks), *self.tiers]
        if self.open_only:
            bits.append("open")
        text = " / ".join(bits)
        if self.top:
            text = f"top {self.top}" + (f" of {text}" if text else "")
        return f"Showing {text} only." if text else ""


@dataclass(frozen=True)
class Command:
    kind: str            # view | why | csv | json | add
    view: View = View()
    text: str = ""


def parse(text: str) -> Command | None:
    """The follow-up this message asks for, or None when it isn't one."""
    raw = text or ""
    add = _ADD.match(raw)
    if add:
        return Command("add", text=raw[add.end():])
    if len(raw.split()) > MAX_COMMAND_WORDS:
        return None
    if _WHY.search(raw):
        return Command("why", text=raw)
    view = _view(raw)
    if _CSV.search(raw):
        return Command("csv", view)
    if view != View() or _RESET.search(raw):
        return Command("json" if _JSON.search(raw) else "view", view)
    if _JSON.search(raw):
        return Command("json", view)
    return None


def _view(text: str) -> View:
    sort = _SORT.search(text)
    sort_by = "priority"
    if sort:
        sort_by = (sort.group(1) or ("age" if sort.group(2) else "risk")).casefold()
    top = _TOP.search(text)
    return View(tuple(sorted({f"P{n}" for n in _PRIORITY.findall(text)})),
                tuple(dict.fromkeys(r.casefold() for r in _RISK.findall(text))),
                tuple(dict.fromkeys(t.casefold() for t in _TIER.findall(text))),
                bool(_OPEN.search(text)), int(top.group(1)) if top else None, sort_by)


def apply(view: View, tickets: Sequence[Ticket]) -> list[Ticket]:
    shown = [t for t in tickets
             if (not view.priorities or t.priority in view.priorities)
             and (not view.risks or t.escalation_risk in view.risks)
             and (not view.tiers or t.tier in view.tiers)
             and (not view.open_only or t.is_open)]
    shown = sorted(shown, key=lambda t: sort_key(t, view.sort))
    return shown[:view.top] if view.top else shown


def find(question: str, tickets: Sequence[Ticket]) -> list[Ticket]:
    """The tickets a "why ...?" names: by id first, else by words matched to category, signals, subject."""
    by_id = {t.id.casefold(): t for t in tickets}
    named = [by_id[m.casefold()] for m in _ID.findall(question) if m.casefold() in by_id]
    if named:
        return list(dict.fromkeys(named))[:MAX_WHY_MATCHES]
    view = _view(question)
    pool = [t for t in tickets if (not view.priorities or t.priority in view.priorities)
            and (not view.risks or t.escalation_risk in view.risks)]
    words = [w for w in _WORD.findall(question.casefold()) if w not in _STOP]
    scored = []
    for t in pool:
        hay = " ".join([t.category, *(s.name for s in t.signals), t.subject, t.body[:300], t.customer or ""]).casefold()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda pair: (-pair[0], sort_key(pair[1])))
    return [t for _, t in scored[:MAX_WHY_MATCHES]]
