"""One humanize pass over one draft:

    detect(code) ─ clean → unchanged, zero calls
                 └ mask → rewrite(1) → postprocess + checks(code) ─ fail → repair(1) ─ fail → original back
                                                                 └ ok → [audit(1) → checks → repair(1)] → outcome

The audit runs only when the result still has density ≥ 2.0 or a chatbot artefact; a failed audit keeps the
checked pre-audit text. At most 4 calls, shared with any other field of the same turn."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import checks
import llm
import prompts
from mask import Masked, mask, token_problems, unmask
from options import Options
from outcome import (
    ENGLISH_ONLY,
    FAILED_CHECK,
    MODEL_FAILED,
    OFF_TOPIC,
    OK,
    Outcome,
    found_phrases,
    removed_counts,
    unchanged,
)
from patterns import Detection, detect
from postprocess import postprocess

AUDIT_DENSITY = 2.0
_ENGLISH = ("en", "eng", "english")


@dataclass(frozen=True)
class Attempt:
    status: str                      # ok | off_topic | english_only | failed
    text: str = ""
    changes: list[dict] = field(default_factory=list)
    dropped: int = 0
    repaired: bool = False
    problems: tuple[str, ...] = ()


async def humanize(deps: llm.TurnDeps, budget: llm.CallBudget, draft: str, options: Options,
                   context: Sequence[str] = ()) -> Outcome:
    before = detect(draft)
    if before.is_clean:
        return unchanged("clean", draft, before)
    masked = mask(draft)
    base = prompts.rewrite_messages(masked.text, options, found_phrases(before), context)
    try:
        first = await _attempt(deps, budget, base, masked, draft, draft, options, before)
    except llm.LlmError:
        return unchanged(MODEL_FAILED, draft, before)
    if first.status in (OFF_TOPIC, ENGLISH_ONLY):
        return unchanged(first.status, draft, before)
    if first.status != OK:
        return unchanged(FAILED_CHECK, draft, before, "; ".join(first.problems[:3]))
    final, audited = first, False
    after = detect(first.text)
    if (after.density >= AUDIT_DENSITY or after.has_artefact) and llm.can_call(deps, budget):
        audit = await _audit(deps, budget, first.text, draft, options, before, after, context)
        if audit is not None:
            final, audited = audit, True
            after = detect(audit.text)
    return Outcome(OK, final.text, before.density, after.density, tuple(_merge(first, final, audited)),
                   first.dropped + (final.dropped if audited else 0), removed_counts(before, after),
                   found_phrases(before), first.repaired or (audited and final.repaired), audited)


async def _audit(deps: llm.TurnDeps, budget: llm.CallBudget, text: str, draft: str, options: Options,
                 before: Detection, after: Detection, context: Sequence[str]) -> Attempt | None:
    masked = mask(text)
    messages = prompts.audit_messages(masked.text, options, found_phrases(after), context)
    try:
        attempt = await _attempt(deps, budget, messages, masked, draft, text, options, before)
    except llm.LlmError:
        return None
    return attempt if attempt.status == OK else None


async def _attempt(deps: llm.TurnDeps, budget: llm.CallBudget, messages: list[dict], masked: Masked, draft: str,
                   change_source: str, options: Options, before: Detection) -> Attempt:
    """One call, checked; one repair call listing the exact problems when it fails and budget allows."""
    tokens = llm.output_tokens(masked.text)
    answer = await llm.call_json(deps, budget, messages, tokens)
    attempt = evaluate(answer.data, masked, draft, change_source, options, before)
    if attempt.status != "failed" or not llm.can_call(deps, budget):
        return attempt
    repair = prompts.repair_messages(messages, answer.raw, attempt.problems)
    repaired = await llm.call_json(deps, budget, repair, tokens)
    second = evaluate(repaired.data, masked, draft, change_source, options, before)
    if second.status == OFF_TOPIC:  # a repair never turns a draft into a refusal
        return Attempt("failed", problems=attempt.problems)
    return Attempt(second.status, second.text, second.changes, second.dropped, True, second.problems)


def evaluate(data: object, masked: Masked, draft: str, change_source: str, options: Options,
             before: Detection) -> Attempt:
    """Code checks on one model answer: contract, language, tokens, facts, length, structure, re-scan."""
    if not isinstance(data, dict) or data.get("status") not in (OK, OFF_TOPIC):
        return Attempt("failed", problems=("the answer was not one JSON object matching the contract",))
    if data["status"] == OFF_TOPIC:
        return Attempt(OFF_TOPIC)
    language = str(data.get("language") or "en").strip().casefold()
    if not language.startswith(_ENGLISH):
        return Attempt(ENGLISH_ONLY)
    model_text = data.get("text")
    if not isinstance(model_text, str) or not model_text.strip():
        return Attempt("failed", problems=("the answer has no text; return the whole edited draft",))
    problems = token_problems(model_text, masked.originals)
    candidate = unmask(postprocess(model_text, draft, options.keep_em_dashes), masked.originals)
    problems += checks.check(draft, candidate, options, before)
    if problems:
        return Attempt("failed", problems=tuple(problems))
    changes, dropped = checks.validate_changes(data.get("changes"), change_source, candidate, masked.originals)
    return Attempt(OK, candidate, changes, dropped)


def _merge(first: Attempt, final: Attempt, audited: bool) -> list[dict]:
    return [*first.changes, *final.changes] if audited else list(first.changes)

