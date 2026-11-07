"""One trace explained: the fenced data the model sees, the call, the code checks and at most one repair call.
The location shown is resolved by code from a parsed frame; the model may only move it to another app frame by
index."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import checks
import frames as frames_mod
import hints
import llm
import prompts
from inputs import fence
from paste import CodeBlock
from snippets import CodeView, candidate_view, tie
from trace_model import Trace

log = logging.getLogger("stack-trace-explainer.explain")

TRACE_PROMPT_CHARS = 6_000
TRACE_HEAD_CHARS, TRACE_TAIL_CHARS = 4_000, 2_000
FRAMES_HEAD, FRAMES_TAIL = 40, 20
STATUSES = ("ok", "off_topic", "not_a_trace")
_VIEW_PREFIX = re.compile(r"^L\d+: ?")
MAX_WORDS = {"likely_cause": 70, "fix_suggestion": 60}
MAX_FIX_LINES = 15
MAX_STEPS = 4


@dataclass
class Explained:
    status: str                          # ok | off_topic | not_a_trace | failed | skipped
    trace: Trace
    candidate: frames_mod.Candidate
    views: tuple[CodeView, ...] = ()
    checked: checks.Checked | None = None
    repairs: int = 0
    moved: bool = False                  # the model picked another app frame and code accepted it
    repaired_problems: int = 0           # check failures sent to the one repair call

    @property
    def view(self) -> CodeView | None:
        return candidate_view(list(self.views), self.candidate.frame)


def frame_table(trace: Trace) -> str:
    rows = []
    for b_index, block in enumerate(trace.blocks):
        role = "ROOT CAUSE" if b_index == trace.root else ("SURFACE" if b_index == trace.surface else "CHAIN")
        rows.append(f"[{role}] {block.headline()}")
        listed = list(block.frames)
        if len(listed) > FRAMES_HEAD + FRAMES_TAIL:
            omitted = len(listed) - FRAMES_HEAD - FRAMES_TAIL
            listed = [*listed[:FRAMES_HEAD], None, *listed[-FRAMES_TAIL:]]
        for frame in listed:
            if frame is None:
                rows.append(f"  ... {omitted} frames omitted")
                continue
            repeat = f" (repeated {frame.repeat} times)" if frame.repeat > 1 else ""
            source = f" | {frame.source}" if frame.source else ""
            rows.append(f"  {frame.origin}: {frame.label()}{repeat}{source}")
    return "\n".join(rows)


def _trace_excerpt(trace: Trace) -> str:
    raw = trace.raw
    if len(raw) <= TRACE_PROMPT_CHARS:
        return raw
    return f"{raw[:TRACE_HEAD_CHARS]}\n... [{len(raw) - TRACE_PROMPT_CHARS:,} characters omitted] ...\n" \
           f"{raw[-TRACE_TAIL_CHARS:]}"


def user_message(trace: Trace, cand: frames_mod.Candidate, views: tuple[CodeView, ...], words: str) -> str:
    app = frames_mod.app_frames(trace)
    app_rows = "\n".join(f"  {i}: {f.label()}" for i, f in enumerate(app)) or "  (none)"
    where = cand.frame.label() if cand.frame else "none (no frame was parsed)"
    led = f"; the call that led there: {cand.led_from.label()}" if cand.led_from else ""
    hint = hints.hint_for(trace.root_block.exception_type, trace.root_block.message) or "(no hint for this error)"
    code = "\n\n".join(_view_text(v) for v in views) or "(no code was pasted)"
    return "\n\n".join([
        f"USER WORDS:\n{fence(words or '(none)', 'REQUEST')}",
        f"LANGUAGE: {trace.language}{' (frameless: no stack frames were found)' if trace.frameless else ''}",
        f"CANDIDATE (computed by code): {where} [{cand.origin}]{led}",
        f"APP FRAMES (frame_index choices, innermost first):\n{app_rows}",
        f"FRAMES:\n{fence(frame_table(trace), 'FRAMES')}",
        f"HINT (background, not evidence): {hint}",
        f"TRACE:\n{fence(_trace_excerpt(trace), 'TRACE')}",
        f"CODE:\n{code}",
    ])


def _view_text(view: CodeView) -> str:
    note = " (line numbers start at 1 in the snippet; they may not match the file)" if view.numbered_from_one else ""
    return f"file: {view.path or 'unlabelled snippet'}{note}\n{fence(view.render(), 'CODE')}"


def validate(data: dict | None) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return None
    if data["status"] != "ok":
        return {"status": data["status"]}
    cause = data.get("likely_cause")
    if not isinstance(cause, str) or not cause.strip():
        return None
    steps = [str(s).strip() for s in data.get("how_to_verify") or () if str(s).strip()][:MAX_STEPS]
    evidence = [e for e in data.get("evidence") or () if isinstance(e, dict) and isinstance(e.get("quote"), str)][:3]
    fix_code = data.get("fix_code") if isinstance(data.get("fix_code"), str) and data["fix_code"].strip() else None
    if fix_code:  # the model sometimes copies the view's "L27: " prefixes into its code
        fix_code = "\n".join(_VIEW_PREFIX.sub("", line) for line in fix_code.strip("\n").splitlines())
    if fix_code and len(fix_code.strip().splitlines()) > MAX_FIX_LINES:
        fix_code = None
    return {"status": "ok", "frame_index": data.get("frame_index"),
            "frame_reason": _text(data.get("frame_reason")), "likely_cause": _words(cause, "likely_cause"),
            "fix_suggestion": _words(_text(data.get("fix_suggestion")) or "", "fix_suggestion"),
            "fix_code": fix_code, "how_to_verify": steps, "evidence": evidence,
            "need_code": data.get("need_code") if isinstance(data.get("need_code"), dict) else None,
            "confidence_notes": _text(data.get("confidence_notes"))}


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _words(text: str, name: str) -> str:
    words = text.split()
    return text.strip() if len(words) <= MAX_WORDS[name] else " ".join(words[:MAX_WORDS[name]]) + " …"


async def explain(deps: llm.TurnDeps, trace: Trace, blocks: tuple[CodeBlock, ...], words: str,
                  policy: tuple[str, ...], may_repair: bool) -> Explained:
    cand = frames_mod.candidate(trace)
    views = tuple(tie(trace, blocks, cand.frame))
    base_user = user_message(trace, cand, views, words)
    messages = [prompts.system(prompts.EXPLAIN_SYSTEM, policy), {"role": "user", "content": base_user}]
    try:
        first = await llm.call_json(deps, messages)
    except llm.LlmError:
        return Explained("failed", trace, cand, views)
    data = validate(first)
    repairs = 0
    if data is None and may_repair and _can_call(deps):
        repairs = 1
        data = validate(await _safe_call(deps, prompts.repair_messages(
            base_user, first or {}, ["the answer was not the JSON object asked for"], policy)))
    if data is None:
        return Explained("failed", trace, cand, views, repairs=repairs)
    if data["status"] != "ok":
        return Explained(data["status"], trace, cand, views, repairs=repairs)
    return await _check(deps, trace, cand, views, data, base_user, policy, may_repair and repairs == 0)


async def _check(deps, trace, cand, views, data, base_user, policy, may_repair) -> Explained:
    chosen, moved = frames_mod.pick(trace, cand, data.get("frame_index"))
    if not moved:
        data = {**data, "frame_reason": None}
    if moved:
        views = tuple(tie(trace, tuple(_blocks_of(views)), chosen.frame))
    ctx = checks.CheckContext(trace, views, chosen.frame)
    checked = checks.run(data, ctx)
    if checked.problems and may_repair and _can_call(deps):
        log.info("explanation failed %d checks; one repair call", len(checked.problems))
        repaired = validate(await _safe_call(deps, prompts.repair_messages(base_user, data, checked.problems,
                                                                           policy)))
        if repaired is not None and repaired["status"] == "ok":
            again = checks.run({**repaired, "frame_index": data.get("frame_index")}, ctx)
            return Explained("ok", trace, chosen, views, again, repairs=1, moved=moved,
                             repaired_problems=len(checked.problems))
        return Explained("ok", trace, chosen, views, checked, repairs=1, moved=moved,
                         repaired_problems=len(checked.problems))
    return Explained("ok", trace, chosen, views, checked, moved=moved)


def _blocks_of(views: tuple[CodeView, ...]) -> list[CodeBlock]:
    return [CodeBlock(v.path, v.lines, v.start, explicit_start=not v.numbered_from_one) for v in views]


def _can_call(deps: llm.TurnDeps) -> bool:
    return deps.budget.left > 0 and deps.has_time()


async def _safe_call(deps: llm.TurnDeps, messages: list[dict]) -> dict | None:
    try:
        return await llm.call_json(deps, messages)
    except llm.LlmError:
        return None
