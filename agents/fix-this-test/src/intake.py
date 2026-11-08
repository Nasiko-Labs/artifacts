"""Pure: reading one turn's input into a decision. Greeting, missing trace context, envelope or pasted
text, constraint words, the failure parse, the file map, follow-up cues, and when to pause for a missing
file or for the failure output. No model call happens here."""

from __future__ import annotations

import re
from typing import Any

import files as f
import prompts
import smalltalk
from failure_parse import other_language
from failure_parse import parse as parse_failure
from hitl import MAX_PAUSES, NEED_FILE, NEED_OUTPUT, Pending
from inputs import Constraints, Incoming, constraints, from_envelope

APPLY_SAME_NOTE = "The user pasted more files and asked to apply the same kind of fix to them."
_RETRY = re.compile(r"\b(?:try|give me|attempt) (?:a |an )?(?:different|another|other) (?:fix|approach|way|solution)"
                    r"|\b(?:different|another) (?:fix|approach)\b", re.IGNORECASE)
_EXPLAIN = re.compile(r"\bexplain (?:the|this|that|your) (?:fix|change|patch)\b|\bwhy did (?:it|the test|this test|"
                      r"that) fail\b|\bwhy (?:does|is) (?:it|the test|this test) fail|\bwhat was wrong\b",
                      re.IGNORECASE)


def reply(text: str) -> dict:
    return {"reply": text, "route": ""}


def read_turn(state: dict, incoming: Incoming, traceparent: str | None) -> dict:
    """State updates for this turn: a fixed reply, a pause, or a route (fix / explain / classify / retry)."""
    text = incoming.text
    if not incoming.attachments:
        canned = smalltalk.reply_to(text)
        if canned is not None:
            return reply(canned)
    if not traceparent:
        return reply(prompts.NO_TRACEPARENT_REPLY)
    rules = constraints(text)
    envelope = from_envelope(text)
    if envelope is not None:
        pasted = f.merge({}, envelope.files)
        failure = envelope.failure
    else:
        pasted = f.split("\n".join([text, *incoming.attachments]))
        failure = pasted.failure
    if not parse_failure(failure).found and not pasted.files:
        return _no_data(state, text)
    if other_language(failure) and not any(p.endswith(".py") for p in pasted.files):
        return reply(prompts.UNSUPPORTED_LANGUAGE_REPLY)
    coverage = [f"Skipped `{p}`: {why}." for p, why in pasted.skipped]
    note = ""
    if not parse_failure(failure).found:
        if not state.get("failure"):
            pending = Pending(NEED_OUTPUT, "", pasted.files, "", rules.to_dict(), incoming.wants_json)
            return {"pending": pending.to_metadata(), "route": ""}
        failure, note = state["failure"], APPLY_SAME_NOTE
        pasted = f.merge(state.get("files") or {}, pasted.files)
    return prepare(failure, pasted.files, rules, incoming.wants_json, coverage=coverage, note=note)


def _no_data(state: dict, text: str) -> dict:
    if other_language(text):
        return reply(prompts.UNSUPPORTED_LANGUAGE_REPLY)
    if state.get("failure") and _RETRY.search(text):
        return {"route": "retry"}
    if state.get("failure") and _EXPLAIN.search(text):
        return {"route": "explain"}
    return {"route": "classify"}


def prepare(failure: str, files: dict[str, str], rules: Constraints, wants_json: bool, *, pauses: int = 0,
            static_reason: str | None = None, coverage: list[str] | None = None, note: str = "") -> dict:
    """Store the parsed input, then pause for a missing file or route to the fix (or explain-only)."""
    info = parse_failure(failure)
    missing = f.missing(info.frames, info.ids, files)
    if (missing or not files) and static_reason is None:
        if pauses < MAX_PAUSES:
            pending = Pending(NEED_FILE, failure, files, missing[0] if missing else "", rules.to_dict(), wants_json,
                              pauses + 1)
            return {"pending": pending.to_metadata(), "route": ""}
        static_reason = "some files the failure names were not pasted"
    coverage = list(coverage or [])
    if info.more_ids:
        coverage.append(f"Only the first 3 failing tests were targeted; {info.more_ids} more were left out.")
    return {"failure": failure, "files": dict(files), "rules": rules.to_dict(), "wants_json": wants_json,
            "static_reason": static_reason, "coverage": coverage, "note": note,
            "route": "explain" if rules.explain_only else "fix"}


def from_answer(pending: Pending, action: str, failure: str, files: dict[str, str]) -> dict:
    static = "you chose to continue without the missing file" if action == "static" else None
    return prepare(failure, files, Constraints.from_dict(pending.rules), pending.wants_json, pauses=pending.pauses,
                   static_reason=static)


def stored_last(state: dict) -> dict[str, Any] | None:
    last = state.get("last")
    return last if isinstance(last, dict) else None
