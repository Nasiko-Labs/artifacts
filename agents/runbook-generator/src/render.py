"""Pure: the markdown runbook, rendered and numbered by code. No raw HTML, no images, links only to URLs that
survived grounding; placeholders are shown as code so they stay visible."""

from __future__ import annotations

import json
import re

import commands
from model import INPUT, Runbook, Step
from placeholders import PLACEHOLDER, fill, found_in

SECTIONS = (("triage", "Triage (read-only)"), ("diagnosis", "Diagnosis (read-only)"),
            ("mitigation", "Mitigation (changes state)"), ("verify", "Verify recovery"), ("escalate", "Escalate"),
            ("after", "After"))
NONE_FOUND = "None found"
FULL, CHECKS, ROLLBACK, RISKY, SHORT, SOURCES = "full", "checks", "rollback", "risky", "short", "sources"
MOSTLY_SUGGESTED = 0.6

_HTML = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]*)\)")
_TICKS = re.compile(r"`[^`]*`")
_LEAD_VERB = re.compile(r"^\s*(?:escalate|page|contact|notify|call)\s+(?:to\s+)?", re.IGNORECASE)
_IF_CLAUSE = re.compile(r"\s+(?:if|when|after)\s.*$", re.IGNORECASE)
_ESCALATE_AFTER = re.compile(r"(?:more than|over|after|longer than|exceeds?|beyond)\s+\d+\s*(?:seconds?|minutes?|mins?|"
                             r"hours?|hrs?)", re.IGNORECASE)


def safe(text: str | None) -> str:
    """Markdown-safe text: images removed, links to anything but a real URL flattened, placeholders kept
    visible as code, and any other tag-shaped text removed."""
    out = _IMAGE.sub("", text or "")
    out = _LINK.sub(lambda m: m.group(0) if m.group(2).startswith("http") else m.group(1), out)
    pieces, last = [], 0
    for tick in _TICKS.finditer(out):
        pieces.append(_plain(out[last:tick.start()]))
        pieces.append(tick.group(0))
        last = tick.end()
    pieces.append(_plain(out[last:]))
    return "".join(pieces).strip()


def _plain(text: str) -> str:
    marked = PLACEHOLDER.sub(lambda m: "\x00" + m.group(0)[1:-1] + "\x01", text)
    cleaned = _HTML.sub("", marked)
    return cleaned.replace("\x00", "`<").replace("\x01", ">`")


def code(text: str) -> str:
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def label(step: Step) -> str:
    return {commands.READ_ONLY: "[read-only]", commands.DESTRUCTIVE: "[DESTRUCTIVE]"}.get(step.kind,
                                                                                       "[changes state]")


def source_tag(step: Step) -> str:
    return "your text" if step.source == INPUT else "suggested"


def render_step(step: Step, runbook: Runbook | None = None, with_sources: bool = False) -> str:
    if step.kind == commands.DESTRUCTIVE:
        head = (f"{step.n}. **DESTRUCTIVE: needs the service owner's approval.** {safe(step.title)}. "
                f"Shown because your text used it; do not run it as a step.")
        lines = [head, f"   - Command (do not run without approval): {code(step.command or '')}"]
    else:
        lines = [f"{step.n}. {label(step)} **{safe(step.title)}** - {safe(step.action)}"]
        if step.command:
            lines.append(f"   - Command: {code(step.command)}")
        lines += [f"   - {name}: {safe(value)}" for name, value in (("Expect", step.expect), ("If not", step.if_not))
                  if value]
    if step.kind != commands.READ_ONLY and step.rollback:
        lines.append(f"   - Rollback: {safe(step.rollback)}")
    if step.review:
        lines.append("   - Review before running: it pipes into a shell, substitutes values or spans several lines.")
    lines.append(f"   - Source: {source_tag(step)}")
    if with_sources and runbook is not None:
        quotes = [runbook.fact(i) for i in step.fact_ids]
        lines += [f'   - Quote: "{safe(f.snippet)}"' for f in quotes if f is not None]
    return "\n".join(lines)


def banner(runbook: Runbook) -> str:
    total = len(runbook.steps)
    from_input = sum(1 for s in runbook.steps if s.source == INPUT)
    if total and (total - from_input) / total > MOSTLY_SUGGESTED:
        return f"> Mostly suggested steps ({total - from_input} of {total}): validate with the service owner."
    where = "incident notes" if runbook.mode == "transcript" else "description"
    return f"> Mostly from your {where} ({from_input} of {total} steps). Suggested steps are marked."


def fixed_lines(runbook: Runbook) -> tuple[str, str, str]:
    """(escalate line, revert line, tell line): written by code with placeholders for what the text lacks."""
    contacts = [_IF_CLAUSE.sub("", _LEAD_VERB.sub("", f.text)).rstrip(". ") for f in runbook.facts
                if f.kind == "contact"]
    thresholds = [_threshold(f.text) for f in runbook.facts if f.kind == "threshold"]
    thresholds = thresholds or [m.group(0) for f in runbook.facts if "escalat" in f.snippet.casefold()
                                for m in [_ESCALATE_AFTER.search(f.snippet)] if m]
    when = "; ".join(dict.fromkeys(thresholds)) if thresholds else "<threshold: fill in>"
    who = "; ".join(contacts) if contacts else "<on-call: fill in>"
    changed = [str(s.n) for s in runbook.steps if s.kind == commands.CHANGES_STATE]
    revert = (f"Revert temporary changes made in step(s) {', '.join(changed)}." if changed
              else "Revert any temporary change you made.")
    lines = (f"Escalate when: {when}. Escalate to: {who}.", revert, "Tell <stakeholders: fill in> it is resolved.")
    for name, value in runbook.filled.items():  # the user's fill-ins reach the code-written lines too
        lines = tuple(fill(line, name, value) for line in lines)
    return lines


def _threshold(text: str) -> str:
    """Just the time limit when the fact states one ("more than 15 minutes"), else the fact's own words."""
    match = _ESCALATE_AFTER.search(text)
    return match.group(0) if match else text.rstrip(". ")


def render(runbook: Runbook, view: str = FULL, notes: tuple[str, ...] = ()) -> str:
    if view == SHORT:
        body = [f"{s.n}. {label(s)} {safe(s.title)}" + (f" - {code(s.command)}" if s.command else "")
                for s in runbook.steps]
        return _join([f"# {safe(runbook.title)} (short)", *body, *_fill(runbook), *notes])
    if view in (CHECKS, ROLLBACK, RISKY):
        return _filtered(runbook, view, notes)
    escalate, revert, tell = fixed_lines(runbook)
    out = [f"# {safe(runbook.title)}", banner(runbook), safe(runbook.purpose),
           "**When to use**\n" + _bullets(f.text for f in runbook.facts if f.kind in ("symptom", "signal")),
           "**Before you start**\n" + _bullets(_tools(runbook))]
    for phase, heading in SECTIONS:
        steps = [render_step(s, runbook, view == SOURCES) for s in runbook.steps if s.phase == phase]
        extra = {"escalate": [f"- {safe(escalate)}"],
                 "after": ["- Capture the timeline for the postmortem.", f"- {safe(revert)}", f"- {safe(tell)}"]}
        out.append(f"**{heading}**\n" + "\n".join((steps + extra.get(phase, [])) or [NONE_FOUND]))
    return _join([*out, *_fill(runbook), *notes])


def _filtered(runbook: Runbook, view: str, notes: tuple[str, ...]) -> str:
    if view == CHECKS:
        picked, heading = [s for s in runbook.steps if s.kind == commands.READ_ONLY], "checks only"
    else:
        picked = [s for s in runbook.steps if s.kind != commands.READ_ONLY]
        heading = "rollback only" if view == ROLLBACK else "high-risk steps only"
    if view == ROLLBACK:
        body = [f"{s.n}. {safe(s.title)}: {safe(s.rollback or 'No rollback recorded')}" for s in picked]
    else:
        body = [render_step(s) for s in picked]
    return _join([f"# {safe(runbook.title)} ({heading})", "\n".join(body) or NONE_FOUND, *_fill(runbook), *notes])


def _tools(runbook: Runbook) -> list[str]:
    seen: dict[str, str] = {}
    for s in runbook.steps:
        words = (s.command or "").split()
        tool = words[1] if words[:1] == ["sudo"] and len(words) > 1 else (words[0] if words else "")
        if tool and tool not in seen:
            seen[tool] = "your text" if s.source == INPUT else "suggested"
    return [f"Access to {code(tool)} (source: {origin})" for tool, origin in seen.items()]


def _bullets(items) -> str:
    lines = [f"- {safe(i)}" for i in items if i]
    return "\n".join(lines) if lines else NONE_FOUND


def _fill(runbook: Runbook) -> list[str]:
    texts = [runbook.title, runbook.purpose, *fixed_lines(runbook)]
    texts += [v or "" for s in runbook.steps for v in (s.title, s.action, s.command, s.expect, s.if_not, s.rollback)]
    names = found_in(texts)
    return ["**Fill these in:** " + ", ".join(code(n) for n in names)] if names else []


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p)


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
