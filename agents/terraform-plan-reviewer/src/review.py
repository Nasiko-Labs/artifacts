"""One review: parse (code) -> rules (code) -> verdict (code) -> [intent check] -> narrate -> a stored state.

The state (a plain dict, checkpointed) holds everything the follow-ups need: the findings, counts, caveats,
apply order and the model's checked wording. The verdict is always recomputed from it in code."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import environment
import narrate
import parse
import resources
import rules_data
import rules_ops
from changes import Change, Finding, ParsedPlan, make
from inputs import Request
from llm import LlmError, TurnDeps
from verdict import apply_order, decide

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
DIGEST_MAX_FINDINGS = 60
PREVENT_DESTROY_NOTE = "`prevent_destroy` is not visible in a plan: check the code if you rely on it."
INTENT_FAILED_NOTE = "I couldn't check which changes relate to the stated intent this time."
PL02_SKIP_HINTS = ("iam", "kms", "route53_zone")


@dataclass(frozen=True)
class Failed:
    """Not a plan (or unreadable JSON): a fixed reply, no state."""
    kind: str      # not_a_plan | json_cut_off


def collect(plan: ParsedPlan, prod: bool, intent: str) -> list[Finding]:
    found: list[Finding] = []
    for change in plan.changes:
        found += rules_data.stateful(change, prod)
        if plan.has_attributes:
            found += rules_data.data_loss(change)
        found += rules_ops.per_change(change, plan.changes)
    flagged = {f.address for f in found}
    for change in plan.changes:
        if change.action == "delete" and change.address not in flagged and \
                not any(h in change.resource_type for h in PL02_SKIP_HINTS):
            found.append(make("PL02", "low", change, "destroyed; it holds no data of its own.",
                              f"Does anything still reference {change.address}?"))
    return found + rules_ops.plan_scope(plan.changes, intent)


def number_findings(findings: Sequence[Finding]) -> list[Finding]:
    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 3))
    return [replace(f, id=f"f{n}") for n, f in enumerate(ordered, start=1)]


def digest(state: dict) -> dict:
    findings = state["findings"][:DIGEST_MAX_FINDINGS]
    return {"verdict": verdict_of(state), "environment": state["environment"],
            "environment_assumed": state["environment_source"] == "assumed", "counts": state["counts"],
            "findings": [{"id": f["id"], "rule": f["rule_id"], "severity": f["severity"], "address": f["address"],
                          "action": f["action"], "attribute": f["attribute"], "reason": f["reason"]}
                         for f in findings],
            "blocking": [f["address"] for f in findings if f["blocking"] and not f["confirmed"]]}


def verdict_of(state: dict) -> str:
    findings = [Finding.from_dict(f) for f in state["findings"]]
    return decide(findings, state["has_changes"], state["has_delete"], state["integrity"])


async def run(deps: TurnDeps, request: Request, policy: Sequence[str]) -> dict | Failed:
    plan = parse.parse(request.plan_text)
    if plan is None:
        cut = "resource_changes" in request.plan_text or '"format_version"' in request.plan_text
        return Failed("json_cut_off" if cut else "not_a_plan")
    env = environment.resolve(request.instruction, request.environment, plan.workspace, plan.changes)
    intent = environment.intent(request.instruction, request.intent)
    findings = collect(plan, env.is_prod, intent)
    state = _base_state(plan, env, intent)
    acting = [c for c in plan.changes if c.action not in ("noop", "read")]
    if intent and acting and deps.traceparent:
        try:
            for addresses, reason in await narrate.intent_check(deps, intent, plan.changes, policy):
                group = Change(address=", ".join(addresses), resource_type="", action="scope",
                               evidence="; ".join(addresses))
                findings.append(make("SC01", "medium", group, f"may not be needed for the intent ({reason}).",
                                     f"Are {', '.join(addresses)} part of this change?"))
        except LlmError:
            state["intent_failed"] = True
    state["findings"] = [f.to_dict() for f in number_findings(findings)]
    if acting and deps.traceparent:
        await _narrate(deps, state, policy)
    return state


async def _narrate(deps: TurnDeps, state: dict, policy: Sequence[str]) -> None:
    try:
        result = await narrate.narrate(deps, digest(state), policy)
    except LlmError:
        state["fell_back"] = True
        return
    if isinstance(result, str):
        state["fell_back"] = True
        return
    state["summary"], state["fell_back"] = result.summary, result.fell_back
    for f in state["findings"]:
        if f["id"] in result.questions:
            f["question"] = result.questions[f["id"]]


def _base_state(plan: ParsedPlan, env: environment.Environment, intent: str) -> dict:
    counts = parse.computed_counts(plan)
    caveats = list(plan.caveats)
    if any(c.is_destructive and resources.is_stateful(c.resource_type) for c in plan.changes):
        caveats.append(PREVENT_DESTROY_NOTE)
    other = sorted({c.resource_type.split("_", 1)[0] for c in plan.changes
                    if c.resource_type and not resources.known_provider(c.resource_type)})
    notes = list(plan.soft_notes)
    if other:
        notes.append(f"Resource types from {', '.join(other)} get action-level checks only (no data tables).")
    return {"format": plan.format, "environment": env.name, "environment_source": env.source, "intent": intent,
            "counts": counts, "findings": [], "caveats": caveats, "notes": notes, "drift": list(plan.drift),
            "has_changes": counts["total"] > 0, "has_delete": counts["destroy"] > 0,
            "integrity": bool(plan.caveats), "hard": plan.hard, "summary": "", "fell_back": False,
            "intent_failed": False, "apply_order": apply_order(plan.changes),
            "addresses": [c.address for c in plan.changes]}
