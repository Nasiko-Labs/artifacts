"""draft_steps (one call over the checked facts), then lint in code, then at most one repair call naming the
failing steps; whatever still fails is rewritten by code."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import lint
import llm
import prompts
from model import Fact, Step
from placeholders import Grounder

log = logging.getLogger("runbook-generator-stateless.draft")

DRAFT_STATUSES = ("ok", "off_topic")


@dataclass(frozen=True)
class Drafted:
    status: str                      # ok | off_topic | failed
    title: str = ""
    purpose: str = ""
    steps: tuple[Step, ...] = ()
    dropped: int = 0
    rewritten: int = 0
    repair_failed: bool = False


def fact_dicts(facts: Sequence[Fact]) -> list[dict]:
    return [{k: v for k, v in asdict(f).items() if v is not None} for f in facts]


def validate_draft(data: object) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in DRAFT_STATUSES:
        return None
    if data["status"] == "ok" and not isinstance(data.get("steps"), list):
        return None
    return data


def validate_steps(data: object) -> dict | None:
    return data if isinstance(data, dict) and isinstance(data.get("steps"), list) else None


async def draft(deps: llm.TurnDeps, facts: tuple[Fact, ...], service: str, mode: str, instruction: str,
                source: str, policy: Sequence[str] = ()) -> Drafted:
    messages = prompts.draft_messages(fact_dicts(facts), service, mode, instruction, policy)
    try:
        data = await llm.complete_json(deps, messages, prompts.DRAFT_CONTRACT, validate_draft)
    except llm.LlmError as e:
        log.warning("draft failed: %s", e)
        return Drafted("failed")
    if data["status"] == "off_topic":
        return Drafted("off_topic")
    raw_steps = list(data.get("steps") or [])
    linted = lint.lint(raw_steps, facts, source)
    repair_failed = False
    if linted.failures:
        linted, repair_failed = await _repair(deps, raw_steps, linted, facts, source, policy)
    steps, rewritten = lint.finalize(linted)
    grounder = Grounder(source)
    title = grounder.ground(str(data.get("title") or "").strip()[:lint.MAX_FIELD_CHARS])
    purpose = grounder.ground(str(data.get("purpose") or "").strip()[:lint.MAX_FIELD_CHARS])
    return Drafted("ok", title, purpose, steps, linted.dropped, rewritten,
                   repair_failed or lint.structural_failures(linted))


def problems(linted: lint.Linted) -> list[str]:
    out = []
    for index, reason in linted.failures:
        out.append(reason if index < 0 else f"step {index + 1} ({linted.steps[index].title}): {reason}")
    return out


async def _repair(deps, raw_steps, linted, facts, source, policy) -> tuple[lint.Linted, bool]:
    """One repair call; on failure (or a worse answer) the first lint result is kept."""
    if not deps.has_time() or not deps.budget.take_repair():
        return linted, True
    shown = [s.to_dict() for s in linted.steps]
    for step in shown:
        step["source_fact_ids"] = step.pop("fact_ids")
    messages = prompts.repair_messages(shown, problems(linted), fact_dicts(facts), policy)
    try:
        data = validate_steps(llm.load(await llm.call(deps, messages)))
    except llm.LlmError as e:
        log.warning("repair failed: %s", e)
        data = None
    if data is None:
        return linted, True
    repaired = lint.lint(data["steps"], facts, source)
    if len(repaired.steps) < len(linted.steps) and len(linted.steps) >= lint.MIN_STEPS:
        return linted, True
    return lint.Linted(repaired.steps, repaired.failures, linted.dropped + repaired.dropped,
                       repaired.replaced), bool(repaired.failures)
