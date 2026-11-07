"""Pure: the incident card (≤ 3,500 chars), the scoring details a follow-up may explain, and the copyable
fallback when a post cannot go out. Rendered in code from the facts; the model writes only the summary
sentence and the fix paraphrases, both checked before they get here."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from windows import minutes_between, parse_time

MAX_CARD_CHARS = 3_500
MAX_TIMELINE = 12
MAX_QUOTE_CHARS = 160
SHORT_SHA = 7
SCORE_RULE = ("Suspect score = kind_weight x (0.45 recency + 0.25 service_match + 0.20 error_overlap + "
              "0.10 error_onset); recency = 0.5^(minutes before start / 60).")
TRIGGER_NOTE = "Run on demand in chat; nothing starts this automatically when a page fires."


def _hhmm(value: str, day: str) -> str:
    """HH:MM, with the month and day in front when the event is not on the start's day."""
    moment = parse_time(value)
    if moment is None:
        return "??:??"
    return moment.strftime("%H:%M") if moment.date().isoformat() == day else moment.strftime("%m-%d %H:%M")


def _ts(value: str) -> float:
    moment = parse_time(value)
    return moment.timestamp() if moment else float("inf")


def _cut(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _change_label(c: Mapping) -> str:
    kind = "deploy" if c.get("kind") == "deploy" else "PR"
    by = f" by {c['author_login']}" if c.get("author_login") else ""
    return f"{kind} {c.get('ref')} {str(c.get('sha'))[:SHORT_SHA]}{by}"


def timeline(f: Mapping) -> list[str]:
    events: list[tuple[str, str]] = []
    for c in (*f.get("suspects", ()), *f.get("later_changes", ())):
        events.append((c["time"], _change_label(c) + ("" if c in f.get("suspects", ()) else " (after the start)")))
    for m in f.get("metrics", ()):
        ratio = f", {m['peak_ratio']}x baseline" if m.get("peak_ratio") else ""
        events.append((m["onset"], f"metric deviates: {_cut(m['label'], 70)}{ratio}"))
    for g in f.get("log_spikes", ()):
        events.append((g["onset"], f"error logs spike ({g['hot_count']} in 30 min): \"{_cut(g['sample'], 90)}\""))
    for e in f.get("errors", ()):
        if e.get("onset"):
            what = "new error" if e["kind"] == "new" else "error rate spike"
            events.append((e["onset"], f"{what} {e['id']}: {_cut(e['title'], 80)}"))
    events.append((f["t0"], "incident started (alert)" if f.get("alerting") else "incident started (time you gave)"))
    ordered = sorted(events, key=lambda ev: _ts(ev[0]))
    day = str(f["t0"])[:10]
    return [f"  {_hhmm(t, day)} {text}" for t, text in ordered[-MAX_TIMELINE:]]


def _suspects(f: Mapping) -> list[str]:
    out = []
    for n, s in enumerate(f.get("suspects", ()), 1):
        out.append(f"  {n}. {_change_label(s)} — {round(s['delta_min'])} min before start (score {s['score']}) "
                   f"{s.get('link', '')}".rstrip())
        out.extend(f"     - {e}" for e in s.get("evidence", ()))
    return out


def _similar(f: Mapping, fixes: Mapping[str, str]) -> list[str]:
    out = []
    for p in f.get("similar", ()):
        date = str(p.get("created_at", ""))[:10]
        if fixes.get(p["id"]):
            quote = f" (\"{_cut(p['resolution_quote'], MAX_QUOTE_CHARS)}\")" if p.get("resolution_quote") else ""
            fixed = f"fixed by: {_cut(fixes[p['id']], MAX_QUOTE_CHARS)}{quote}"
        elif p.get("resolution_quote"):
            fixed = f"fixed by: \"{_cut(p['resolution_quote'], MAX_QUOTE_CHARS)}\""
        else:
            fixed = "no resolution note"
        link = f" [postmortem {p['postmortem_url']}]" if p.get("postmortem_url") else ""
        out.append(f"  - {p['id']} {_cut(p.get('title', ''), 80)} ({date}) — {fixed}{link}")
    return out


def render(f: Mapping, summary: str, fixes: Mapping[str, str], now: datetime, note: str = "") -> str:
    inc = f["incident"]
    t0 = parse_time(f["t0"])
    ago = round(minutes_between(t0, now)) if t0 else 0
    head = " · ".join(x for x in (f"INCIDENT {inc.get('id') or '(no id)'}", inc.get("title"), inc.get("status")) if x)
    lines = [f"{head} {inc.get('url') or ''}".rstrip(), f"What's broken: {summary}",
             f"Started: {f['t0']} ({ago} min ago) · Service: {f.get('service') or 'unknown'}",
             "Timeline (UTC):", *timeline(f)]
    lines += ["Top suspects:", *_suspects(f)] if f.get("suspects") else []
    lines += f.get("notes", [])
    similar = _similar(f, fixes)
    lines += ["Similar past incidents:", *similar] if similar else ["Similar past incidents: none found"]
    lines.append(f"Runbook: {f.get('runbook') or 'none linked'}")
    gaps = list(f.get("coverage", ()))
    if f.get("dropped"):
        gaps.append(f"{f['dropped']} record(s) were left out because a tool result did not back them.")
    lines += ["Coverage:", *(f"  - {g}" for g in gaps)] if gaps else []
    lines.append(TRIGGER_NOTE)
    text = "\n".join(([note] if note else []) + lines)
    return text if len(text) <= MAX_CARD_CHARS else text[: MAX_CARD_CHARS - 1] + "…"


def details(f: Mapping) -> str:
    """The scoring breakdown a follow-up may explain ("why is suspect 2 ranked there")."""
    out = [SCORE_RULE]
    for n, s in enumerate(f.get("suspects", ()), 1):
        parts = ", ".join(f"{k}={v}" for k, v in s.get("parts", {}).items())
        out.append(f"Suspect {n} ({_change_label(s)}): score {s['score']}; {parts}; files: "
                   f"{', '.join(s.get('files') or ()) or 'unknown'}")
    return "\n".join(out)


def copyable(card: str, reason: str) -> str:
    return f"{reason} Here is the card to copy:\n\n{card}"
