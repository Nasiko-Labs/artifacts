"""One fix run: materialize → baseline → (flaky diagnosis | loop) → verify, with every run in the sandbox
and every count computed here. The run directory is deleted at the end, also on an exception."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import diagnose
import files as f
import runner
from failure_parse import Failure, missing_modules
from failure_parse import parse as parse_failure
from inputs import Constraints
from llm import Budget, TurnDeps
from loop import Job, LoopOutcome, run_loop
from runner import Results
from sandbox import Sandbox

MAX_SANDBOX_RUNS = 8
FLAKY_RERUNS = 3
NO_RULES = Constraints()


@dataclass(frozen=True)
class FixRequest:
    failure: str
    files: dict[str, str]
    rules: Constraints = NO_RULES
    org: tuple[str, ...] = ()
    static_reason: str | None = None
    note: str = ""


@dataclass
class FixOutcome:
    mode: str = "sandbox"
    static_reason: str = ""
    targets: tuple[str, ...] = ()
    before: Results | None = None
    after: Results | None = None
    loop: LoopOutcome | None = None
    verified: bool = False
    flaky: diagnose.Diagnosis | None = None
    notes: list[str] = field(default_factory=list)



class _Runs:
    def __init__(self, sandbox: Sandbox, root: Path):
        self.sandbox, self.root, self.used = sandbox, root, 0

    async def run(self, files_now: dict[str, str], args: list[str]) -> Results:
        self.used += 1
        work = Sandbox.materialize(self.root, f"run-{self.used}", files_now)
        output = await asyncio.to_thread(self.sandbox.run, self.root, work, args)
        return runner.parse(output.output, output.timed_out)


async def fix(deps: TurnDeps, budget: Budget, sandbox: Sandbox, req: FixRequest) -> FixOutcome:
    pasted = parse_failure(req.failure)
    test_files = tuple(p for p in req.files if f.is_test_file(p) or _holds_target(p, pasted))
    reason = req.static_reason or (None if sandbox.available() else "the run directory is not writable here")
    if reason:
        return await _static(deps, budget, req, pasted, test_files, reason)
    root = sandbox.new_root()
    try:
        return await _sandboxed(deps, budget, req, pasted, test_files, _Runs(sandbox, root))
    finally:
        Sandbox.remove(root)


async def _static(deps, budget, req: FixRequest, pasted: Failure, test_files, reason: str) -> FixOutcome:
    facts = (f"Mode: static. Tests cannot run here ({reason}); propose the edit without running, then finish.",)
    job = Job(req.failure, req.files, test_files, pasted.ids, req.rules, req.org, None, facts)
    return FixOutcome(mode="static", static_reason=reason, targets=pasted.ids, loop=await run_loop(deps, budget, job))


async def _sandboxed(deps, budget, req: FixRequest, pasted: Failure, test_files, runs: _Runs) -> FixOutcome:
    all_args = runner.command("all", (), test_files)
    before = await runs.run(req.files, all_args)
    third_party = [m for m in missing_modules(before.output) if not _is_pasted_module(m, req.files)]
    if third_party:
        reason = f"the tests need `{third_party[0]}`, which isn't installed here (standard library and pytest only)"
        return await _static(deps, budget, req, pasted, test_files, reason)
    targets = tuple(t for t in (before.find(i) for i in pasted.ids) if t) or before.ids_with("FAILED") \
        or before.ids_with("ERROR")
    outcome = FixOutcome(targets=targets, before=before)
    _note_differences(outcome, pasted, before)
    if targets and all(before.status(t) == "PASSED" for t in targets):
        passes = await _reruns(runs, req.files, targets, test_files)
        if passes > 0:
            outcome.flaky = await diagnose.flaky(deps, budget, req.failure, req.files, passes, FLAKY_RERUNS, req.org)
            outcome.after = before
            return outcome
    facts = (f"Baseline run (computed): {before.failed} failed, {before.passed} passed, {before.errors} errors.",
             *outcome.notes, *([req.note] if req.note else []))

    async def run_tests(scope: str, now: dict[str, str]) -> Results | str:
        if runs.used >= MAX_SANDBOX_RUNS - 1:
            return "error: no test runs left this turn; finish now"
        return await runs.run(now, runner.command(scope, targets, test_files))

    job = Job(req.failure, req.files, test_files, targets, req.rules, req.org, run_tests, facts)
    outcome.loop = await run_loop(deps, budget, job)
    if outcome.loop.off_topic:
        return outcome
    outcome.after = await runs.run(outcome.loop.finals, all_args)
    outcome.verified = verified(targets, before, outcome.after)
    return outcome


def verified(targets: tuple[str, ...], before: Results, after: Results) -> bool:
    """Every baseline-failing target passes, and no baseline-passing test fails."""
    if after.timed_out or not targets:
        return False
    if any(after.status(t) != "PASSED" for t in targets):
        return False
    return all(after.status(t) not in ("FAILED", "ERROR") for t in before.ids_with("PASSED"))


async def _reruns(runs: _Runs, files_now: dict[str, str], targets, test_files) -> int:
    passes = 0
    for _ in range(FLAKY_RERUNS):
        result = await runs.run(files_now, runner.command("failing", targets, test_files))
        passes += all(result.status(t) == "PASSED" for t in targets)
    return passes


def _note_differences(outcome: FixOutcome, pasted: Failure, before: Results) -> None:
    for target in outcome.targets:
        tid = before.find(target)
        got = before.outcomes[tid][1] if tid else None
        expected = pasted.exception_for(target)
        if got and expected and got != expected:
            outcome.notes.append(f"Here `{target}` fails with {got}, not {expected} as in your paste.")
    if before.timed_out:
        outcome.notes.append("The baseline run hit the time limit.")


def _holds_target(path: str, pasted: Failure) -> bool:
    return any("::" in t and f.resolve(t.split("::", 1)[0], {path: ""}) for t in pasted.ids)


def _is_pasted_module(module: str, pasted: dict[str, str]) -> bool:
    top = module.split(".", 1)[0]
    return any(p.split("/")[-1] == f"{top}.py" or f"/{top}/" in f"/{p}" for p in pasted)
