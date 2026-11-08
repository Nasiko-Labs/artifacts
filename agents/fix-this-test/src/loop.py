"""The bounded edit loop (mini-swe-agent's method, reimplemented): one JSON action per model call, code
validates and guards it, applies it or runs the tests, and feeds the observation back. It ends on
`finish`, the step cap, the deadline or the call budget. The original files are never changed."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import actions
import edits
import guards
import prompts
from inputs import Constraints, fence_text
from llm import Budget, BudgetExhausted, TurnDeps, ask_json, system_with_org
from runner import Results

log = logging.getLogger("fix-this-test.loop")

MAX_STEPS = 8
MAX_FAILURE_CHARS = 4_000
MAX_OBSERVATION_CHARS = 3_000
SHOWN_OBSERVATIONS = 6

RunTests = Callable[[str, dict[str, str]], Awaitable[Results | str]]


@dataclass(frozen=True)
class Job:
    failure: str
    originals: dict[str, str]
    test_files: tuple[str, ...]
    targets: tuple[str, ...]
    rules: Constraints
    org: tuple[str, ...] = ()
    run: RunTests | None = None          # None in static mode
    facts: tuple[str, ...] = ()          # computed lines for the model (baseline counts, mode, notes)


@dataclass
class LoopOutcome:
    finals: dict[str, str]
    finished: bool = False
    explanation: str = ""
    off_topic: bool = False
    steps: int = 0
    changed_test: bool = False
    special_casing: bool = False
    refusals: list[str] = field(default_factory=list)
    stop: str = "step_cap"


async def run_loop(deps: TurnDeps, budget: Budget, job: Job) -> LoopOutcome:
    out = LoopOutcome(finals=dict(job.originals))
    observations: list[str] = []
    repaired = False
    for step in range(MAX_STEPS):
        if budget.left <= 1 or not budget.can_call():  # keep one call for the explanation
            out.stop = "budget"
            break
        out.steps = step + 1
        messages = _messages(job, out.finals, observations, MAX_STEPS - step)
        try:
            data = await ask_json(deps, budget, messages)
            action = actions.parse(data)
            if isinstance(action, str) and not repaired and budget.left > 1:
                repaired = True
                retry = [*messages, {"role": "system", "content": prompts.REPAIR_JSON_SYSTEM}]
                action = actions.parse(await ask_json(deps, budget, retry))
        except BudgetExhausted:
            out.stop = "budget"
            break
        if isinstance(action, str):
            log.info("step %d invalid: %s", step + 1, action)
            observations.append(f"[step {step + 1}] {action}")
            continue
        if action.kind == "off_topic":
            if step == 0:
                out.off_topic, out.stop = True, "off_topic"
                return out
            observations.append(f"[step {step + 1}] error: keep working on the failing test")
            continue
        if action.kind == "finish":
            out.finished, out.explanation, out.stop = True, action.explanation, "finish"
            return out
        observation = await _act(job, out, action)
        log.info("step %d %s: %s", step + 1, action.kind, observation[:160].replace("\n", " | "))
        observations.append(f"[step {step + 1}] {action.kind}: {observation[:MAX_OBSERVATION_CHARS]}")
    return out


async def _act(job: Job, out: LoopOutcome, action: actions.Action) -> str:
    if action.kind == "read":
        return edits.read(out.finals, action)
    if action.kind == "run":
        if job.run is None:
            return "error: tests cannot run in this mode; propose the edit, then finish"
        result = await job.run(action.scope, dict(out.finals))
        return result if isinstance(result, str) else _run_observation(result)
    applied = edits.apply(out.finals, action)
    if applied.text is None:
        return applied.observation
    verdict = guards.check(action, applied.text, out.finals, job.originals, job.test_files, job.rules)
    if verdict.refusal:
        out.refusals.append(verdict.refusal)
        return verdict.refusal
    out.finals[action.path] = applied.text
    out.changed_test = out.changed_test or verdict.test_edit
    out.special_casing = out.special_casing or verdict.special_casing
    return applied.observation + ("; now run the tests" if job.run else "; finish when the change is complete")


def _run_observation(result: Results) -> str:
    head = (f"tests: {result.failed} failed, {result.passed} passed, {result.errors} errors"
            + (" (timed out)" if result.timed_out else ""))
    if result.passed and not (result.failed or result.errors or result.timed_out):
        head += ". Every test in this run passes: return finish now with your explanation."
    tail = result.output[-(MAX_OBSERVATION_CHARS - len(head) - 40):]
    return f"{head}\n{fence_text(tail)}"


def _messages(job: Job, finals: dict[str, str], observations: list[str], steps_left: int) -> list[dict]:
    listing = "\n".join(f"- {p} ({len(t.splitlines())} lines){' [test]' if p in job.test_files else ''}"
                        f"{' [edited]' if t != job.originals[p] else ''}" for p, t in finals.items())
    rules = [*(["The user said: do not change the tests."] if job.rules.no_test_edits else []),
             *([f"The user allows edits only to: {', '.join(job.rules.only_edit)}."] if job.rules.only_edit else [])]
    earlier = len(observations) - SHOWN_OBSERVATIONS
    shown = observations[-SHOWN_OBSERVATIONS:]
    user = "\n".join([
        "Make the failing test(s) pass by fixing the cause in the source code.",
        f"Failing tests (computed): {', '.join(job.targets) or 'see the output'}",
        *job.facts, *rules,
        "Files:", listing,
        "Failure output (first 4,000 characters):", fence_text(job.failure[:MAX_FAILURE_CHARS]),
        "Observations so far:" if shown else "No actions yet.",
        *([f"({earlier} earlier observations not shown)"] if earlier > 0 else []),
        *[fence_text(o) for o in shown],
        f"Steps left: {steps_left}. Return one JSON action.",
    ])
    return [{"role": "system", "content": system_with_org(prompts.LOOP_SYSTEM, job.org)},
            {"role": "user", "content": user}]
