"""One humanize pass over one draft:

    detect(code) ─ clean → unchanged, zero calls
                 └ mask → rewrite(1) → postprocess + checks(code) ─ fail → repair(1) ─ fail → original back
                                                                 └ ok → [audit(1) → checks → repair(1)] → outcome

The audit (one re-run) runs whenever counted AI patterns remain in the checked result, and the density reported
is always measured on the text returned; a failed audit keeps the checked pre-audit text. "Shorter" carries a word
cap (`Options.max_words`, below the last version or the draft) that the length check enforces; when the model is
still over it after the repair, code cuts filler (`shorten.py`), so "shorter" never returns the text unchanged.
At most 4 calls, shared with any other field of the same turn."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import checks
import llm
import prompts
import shorten
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
from patterns import Detection, detect, word_count
from postprocess import postprocess

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
    if before.is_clean and not options.shorter:   # "shorter" is a real edit even on a clean draft
        return unchanged("clean", draft, before)
    options = with_shorter_cap(draft, options)
    masked = mask(draft)
    base = prompts.rewrite_messages(masked.text, options, found_phrases(before), context)
    try:
        first = await _attempt(deps, budget, base, masked, draft, draft, options, before)
    except llm.LlmError:
        return cut_in_code(draft, Attempt("failed"), options, before) if options.shorter else \
            unchanged(MODEL_FAILED, draft, before)
    if first.status in (OFF_TOPIC, ENGLISH_ONLY):
        return unchanged(first.status, draft, before)
    if first.status != OK and options.shorter:   # shortening never gives up: code cuts what the model could not
        return cut_in_code(draft, first, options, before)
    if first.status != OK:
        return unchanged(FAILED_CHECK, draft, before, "; ".join(first.problems[:3]))
    final, audited = first, False
    after = detect(first.text)
    if remaining(after, options) and llm.can_call(deps, budget):
        audit = await _audit(deps, budget, first.text, draft, options, before, after, context)
        if audit is not None:
            final, audited = audit, True
            after = detect(audit.text)
    return Outcome(OK, final.text, before.density, after.density, tuple(_merge(first, final, audited)),
                   first.dropped + (final.dropped if audited else 0), removed_counts(before, after),
                   found_phrases(before), first.repaired or (audited and final.repaired), audited)


def with_shorter_cap(draft: str, options: Options) -> Options:
    """"Shorter" with no cap from the last version: cap it below the draft (without its chatbot sentences)."""
    if not options.shorter or options.max_words:
        return options
    return replace(options, max_words=checks.shorter_cap(word_count(checks.kept_text(draft))))


def cut_in_code(draft: str, failed: Attempt, options: Options, before: Detection) -> Outcome:
    """"Shorter" after the model stayed over the cap (or failed): cut filler in code from the model's version when
    its only problem was length, else from the draft. Every fact is kept, so the user always gets a shorter text."""
    usable = failed.text and all(p.startswith(checks.TOO_LONG) for p in failed.problems)
    text = shorten.cut_to(failed.text, options.max_words) if usable else ""
    if not text or checks.fact_problems(draft, text) or word_count(text) >= word_count(draft):
        text = shorten.cut_to(draft, options.max_words)
    after = detect(text)
    return Outcome(OK, text, before.density, after.density, removed=removed_counts(before, after),
                   found=found_phrases(before), repaired=True, cut_in_code=True)


def remaining(after: Detection, options: Options) -> bool:
    """Counted AI patterns still in the text (em dashes the user asked to keep do not count)."""
    return any(not (options.keep_em_dashes and h.category == "em_dash") for h in after.hits)


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
        return attempt
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
        return Attempt("failed", candidate, problems=tuple(problems))
    changes, dropped = checks.validate_changes(data.get("changes"), change_source, candidate, masked.originals)
    return Attempt(OK, candidate, changes, dropped)


def _merge(first: Attempt, final: Attempt, audited: bool) -> list[dict]:
    return [*first.changes, *final.changes] if audited else list(first.changes)

