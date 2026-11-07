"""Write: one call over the checked items only (ids e1.., f1.., a1..) for the prose sections. Code checks
every cited id and every number, time and name; a summary or impact paragraph that fails falls back to a
code template, a list entry that fails is dropped and counted. Timeline, metrics and action items are
rendered by code, never written by the model."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import checks
import grounding as g
import llm
import prompts
from merge import Merged
from metrics import Metrics
from records import ACTION_TYPES, TBD, Action

log = logging.getLogger("postmortem.write")

LIST_KEYS = ("went_well", "went_poorly", "lucky")
WORD_CAPS = {"summary": 60, "impact": 50, "list": 25, "question": 25, "title": 8, "suggestion": 20}
MAX_MODEL_QUESTIONS = 3
MAX_SUGGESTIONS = 5
UNTITLED = "Untitled incident <title?>"
IMPACT_NOT_QUANTIFIED = "Impact not quantified in the log."


@dataclass(frozen=True)
class Prose:
    title: str = ""
    summary: tuple[str, tuple[str, ...]] = ("", ())          # (text, cited ids)
    impact: tuple[str, tuple[str, ...]] = ("", ())
    lists: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]] = field(default_factory=dict)
    questions: tuple[str, ...] = ()
    suggested: tuple[Action, ...] = ()
    dropped: int = 0
    failed: bool = False


def items_block(merged: Merged, metrics: Metrics) -> str:
    lines = ["EVENTS"]
    lines += [f"{e.id} [{e.time_text or 'untimed'}{' ' + e.tz if e.tz and e.tz not in e.time_text else ''}] "
              f"({e.category}) {e.text}" for e in merged.events]
    lines += ["", "FACTORS"]
    lines += [f"{f.id} ({f.cls}{', inferred' if f.inferred else ''}) {f.text} [events: {', '.join(f.event_ids)}]"
              for f in merged.factors] or ["none"]
    lines += ["", "ACTIONS (from the log)"]
    lines += [f"{a.id} {a.title}; owner {a.owner}; due {a.due}; factors {', '.join(a.factor_ids) or 'none'}"
              for a in merged.actions] or ["none"]
    lines += ["", "METRICS (fixed strings: copy exactly, never compute)"]
    lines += [f"{key.replace('_', ' ')}: {value}" for key, value in metrics.values.items()]
    return "\n".join(lines)


def parse_write(data: Any) -> dict | None:
    if not isinstance(data, Mapping):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic"}
    if not all(isinstance(data.get(k) or {}, Mapping) for k in ("summary", "impact")):
        return None
    lists = ("open_questions", "suggested_actions", *LIST_KEYS)
    if not all(isinstance(data.get(k) or [], list) for k in lists):
        return None
    parsed = {"status": "ok", "title": str(data.get("title") or ""), "summary": dict(data.get("summary") or {}),
              "impact": dict(data.get("impact") or {})}
    for key in lists:
        parsed[key] = [dict(e) for e in data.get(key) or [] if isinstance(e, Mapping)]
    return parsed


async def write(deps: llm.TurnDeps, merged: Merged, metrics: Metrics, instruction: str, haystack: str,
                context: Sequence[str] = ()) -> Prose:
    messages = prompts.write_messages(items_block(merged, metrics), instruction, context)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.WRITE_CONTRACT, parse_write, llm.WRITE_MAX_TOKENS)
    except llm.LlmError:
        log.warning("write call failed; the prose falls back to code templates")
        return Prose(failed=True)
    if parsed["status"] != "ok":
        return Prose(failed=True)
    return check_prose(parsed, merged, haystack)


def check_prose(parsed: Mapping[str, Any], merged: Merged, haystack: str) -> Prose:
    norm = g.normalise(haystack)
    known = {e.id for e in merged.events} | {f.id for f in merged.factors} | {a.id for a in merged.actions}
    dropped = 0

    def entry(data: Mapping[str, Any], cap: int) -> tuple[str, tuple[str, ...]] | None:
        text = checks.clean_text(data.get("text"), haystack)
        ids = tuple(str(i) for i in data.get("ids") or () if str(i) in known)
        if not text or not ids or g.ungrounded_tokens(text, norm):
            return None
        return checks.cut_words(text, cap), ids

    summary, impact = entry(parsed["summary"], WORD_CAPS["summary"]), entry(parsed["impact"], WORD_CAPS["impact"])
    dropped += (summary is None and bool(parsed["summary"])) + (impact is None and bool(parsed["impact"]))
    lists = {}
    for key in LIST_KEYS:
        kept = [entry(e, WORD_CAPS["list"]) for e in parsed[key]]
        dropped += kept.count(None)
        lists[key] = tuple(k for k in kept if k)
    questions = []
    for q in parsed["open_questions"][:MAX_MODEL_QUESTIONS]:
        text = checks.clean_text(q.get("text"), haystack)
        ok = text and g.locate(str(q.get("snippet") or ""), norm) and not g.ungrounded_tokens(text, norm)
        dropped += not ok
        questions += [checks.cut_words(text, WORD_CAPS["question"])] if ok else []
    suggested, lost = suggestions(parsed["suggested_actions"], merged, norm, haystack)
    return Prose(title_of(parsed.get("title"), norm, haystack), summary or ("", ()), impact or ("", ()), lists,
                 tuple(questions), suggested, dropped + lost)


def title_of(raw: Any, norm: str, haystack: str) -> str:
    title = checks.cut_words(checks.clean_text(raw, haystack), WORD_CAPS["title"]).rstrip(".")
    return title if title and not g.ungrounded_tokens(title, norm) else ""


def suggestions(entries: Sequence[Mapping[str, Any]], merged: Merged, norm: str,
                haystack: str) -> tuple[tuple[Action, ...], int]:
    """At most MAX_SUGGESTIONS, one per factor that no log action covers; owner and due TBD."""
    covered = {fid for a in merged.actions for fid in a.factor_ids}
    factors = {f.id: f for f in merged.factors}
    kept: list[Action] = []
    dropped = 0
    for data in entries:
        fid = str(data.get("factor_id") or "")
        title = checks.cut_words(checks.clean_text(data.get("title"), haystack), WORD_CAPS["suggestion"])
        taken = {a.factor_ids[0] for a in kept}
        if fid not in factors or fid in covered or fid in taken or not title or g.ungrounded_tokens(title, norm) \
                or len(kept) >= MAX_SUGGESTIONS:
            dropped += 1
            continue
        kind = str(data.get("type") or "").casefold()
        kept.append(Action(id="", title=title, owner=TBD, due=TBD, type=kind if kind in ACTION_TYPES else "prevent",
                           source="suggested", factor_ids=(fid,), snippet=factors[fid].snippet))
    return tuple(kept), dropped
