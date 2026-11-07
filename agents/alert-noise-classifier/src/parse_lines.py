"""Pure: plain alert lines and alert-bot chat pastes → `Fire` records.

Plain line: "2026-11-03 02:14 HighMemory web-3 resolved after 3m, nobody acked" — a time, a name token, an
optional host, then optional phrases (resolved after N, auto-resolved, acked by X, no action, false positive,
note: ...). Chat paste: `[FIRING:1] Name key=value ...` / `[RESOLVED] Name ...` lines, each with a time; a
RESOLVED closes the earliest open FIRING with the same name and labels."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from fires import Fire, finish, parse_duration, parse_time

MAX_ROWS = 5_000
TIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s?Z|\s?UTC|[+-]\d{2}:?\d{2})?")
_BOT = re.compile(r"\[(FIRING|RESOLVED)(?::\d+)?\]\s*([A-Za-z][\w.:-]*)(.*)", re.IGNORECASE)
_NAME = re.compile(r"^[A-Za-z][\w.:-]*[A-Za-z0-9]$")
_HOST = re.compile(r"^[a-z0-9][\w.-]*(?:\d|-[a-z0-9])[\w.-]*$")
_STOP = frozenset({"alert", "alerts", "firing", "fired", "resolved", "warning", "critical", "info", "page", "error"})
_SEVERITY = re.compile(r"\b(critical|high|warning|low|page|p[1-5]|sev-?[1-5])\b", re.IGNORECASE)
_SERVICE = re.compile(r"\b(?:service|svc|job)\s*[=:]\s*([\w.-]+)", re.IGNORECASE)
_KV = re.compile(r"\b([a-z_]+)=([\w.-]+)", re.IGNORECASE)
_UNITS = r"(?:s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?)"
_AMOUNT = rf"\d+(?:\.\d+)?\s*{_UNITS}\b(?:\s*\d+(?:\.\d+)?\s*{_UNITS}\b)*"
_DURATION = re.compile(rf"\b(?:resolved|recovered|cleared)\s+after\s+({_AMOUNT})|\b(?:lasted|duration[:=]?)\s+({_AMOUNT})",
                       re.IGNORECASE)
_AUTO = re.compile(r"\bauto[- ]?resolved\b", re.IGNORECASE)
_NO_ACK = re.compile(r"\b(?:nobody|no one|noone)\s+ack(?:ed|nowledged)?\b|\bno\s+ack\b|\bun-?acked\b|\bnot\s+acked\b",
                     re.IGNORECASE)
_ACK_BY = re.compile(r"\back(?:ed|'d|nowledged)?\s+by\s+@?([\w.-]+)", re.IGNORECASE)
_ACK = re.compile(r"\back(?:ed|'d|nowledged)\b", re.IGNORECASE)
_NOTE = re.compile(r"\bnotes?\s*[:=]\s*(.+)$", re.IGNORECASE)
_RESOLVED_WORD = re.compile(r"\b(?:resolved|recovered|cleared)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]{2,}")
_FILLER = frozenset({"resolved", "recovered", "firing", "fired", "alert", "on", "at", "in", "after", "by", "and",
                     "the", "was", "is", "it", "acked", "ack", "status", "triggered"})


@dataclass(frozen=True)
class LinesResult:
    fires: tuple[Fire, ...]
    rows_seen: int = 0
    rows_skipped: int = 0
    notes: tuple[str, ...] = ()


def is_bot_paste(text: str) -> bool:
    return bool(re.search(r"\[(?:FIRING|RESOLVED)(?::\d+)?\]", text or "", re.IGNORECASE))


def parse(text: str) -> LinesResult:
    lines = [x for x in (text or "").splitlines() if x.strip()]
    if is_bot_paste(text):
        return _bot(lines)
    fires, skipped, seen = [], 0, 0
    for i, line in enumerate(lines[:MAX_ROWS]):
        if not TIME.search(line):
            continue
        seen += 1
        fire = plain_line(line, i + 1)
        if fire is None:
            skipped += 1
        else:
            fires.append(fire)
    notes = (f"Only the first {MAX_ROWS} lines were read.",) if len(lines) > MAX_ROWS else ()
    return LinesResult(tuple(fires), seen, skipped, notes)


def plain_line(line: str, index: int) -> Fire | None:
    match = TIME.search(line)
    fired = parse_time(match.group(0)) if match else None
    if fired is None:
        return None
    rest = (line[:match.start()] + " " + line[match.end():]).strip()
    tokens = [t.strip(",;()[]") for t in rest.split()]
    name_at = next((i for i, t in enumerate(tokens) if _NAME.match(t) and t.casefold() not in _STOP
                    and "=" not in t), None)
    if name_at is None:
        return None
    name = tokens[name_at]
    nxt = tokens[name_at + 1] if name_at + 1 < len(tokens) else ""
    host = nxt if _HOST.match(nxt) and "=" not in nxt else ""
    words = " ".join(t for t in tokens if t not in (name, host))
    return finish(Fire(row=index, name=name, t_fire=fired, instance=host, raw=line.strip(), **_phrases(words)))


def _phrases(rest: str) -> dict:
    duration_match = _DURATION.search(rest)
    duration = parse_duration((duration_match.group(1) or duration_match.group(2)).strip(), 60.0) \
        if duration_match else None
    service, severity = _SERVICE.search(rest), _SEVERITY.search(rest)
    ack_by = None if _NO_ACK.search(rest) else _ACK_BY.search(rest)
    acked = bool(ack_by) or (not _NO_ACK.search(rest) and bool(_ACK.search(rest)))
    note_match = _NOTE.search(rest)
    note = note_match.group(1).strip() if note_match else _leftover(rest)
    resolved = duration is not None or bool(_AUTO.search(rest)) or bool(_RESOLVED_WORD.search(rest))
    return {"duration_s": duration, "service": service.group(1) if service else "",
            "severity": severity.group(1) if severity else "", "acked": acked,
            "acked_by": ack_by.group(1) if ack_by else "", "note": note, "status": "resolved" if resolved else ""}


def _leftover(rest: str) -> str:
    """Words that are not a recognised phrase (name and host already removed): kept as a note only when they say something
    a responder would write (no action, false positive, restarted ...)."""
    text = rest
    for pattern in (_DURATION, _NO_ACK, _ACK_BY, _SERVICE, _KV):
        text = pattern.sub(" ", text)
    auto = _AUTO.search(text)
    words = [w for w in _WORD.findall(_AUTO.sub(" ", _SEVERITY.sub(" ", text))) if w.casefold() not in _FILLER]
    phrase = " ".join(words)
    return "auto-resolved" if auto and not phrase else phrase


def _bot(lines: list[str]) -> LinesResult:
    fires: list[Fire] = []
    open_at: dict[tuple, list[int]] = {}
    skipped = seen = 0
    for i, line in enumerate(lines[:MAX_ROWS]):
        bot = _BOT.search(line)
        if not bot:
            continue
        seen += 1
        kind, name, tail = bot.group(1).upper(), bot.group(2), bot.group(3)
        when = TIME.search(line)
        t = parse_time(when.group(0)) if when else None
        labels = dict(_KV.findall(tail))
        key = (name.casefold(), tuple(sorted(labels.items())))
        if t is None:
            skipped += 1
        elif kind == "FIRING":
            open_at.setdefault(key, []).append(len(fires))
            fires.append(Fire(row=i + 1, name=name, t_fire=t, service=labels.get("service", labels.get("job", "")),
                              severity=labels.get("severity", ""), instance=labels.get("instance", ""),
                              labels=tuple(sorted((k, v) for k, v in labels.items() if k in ("env", "cluster"))),
                              raw=line.strip()))
        elif open_at.get(key):
            at = open_at[key].pop(0)
            fires[at] = finish(replace(fires[at], t_resolve=t, status="resolved"))
        else:
            skipped += 1
    return LinesResult(tuple(fires), seen, skipped)
