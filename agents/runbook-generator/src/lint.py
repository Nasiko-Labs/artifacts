"""Pure: the code rules every drafted step goes through: command classification (fail closed), the rollback
rule, destructive handling, host/URL/number grounding, provenance, ordering and the 5-25 step count."""

from __future__ import annotations

from dataclasses import dataclass

import commands
import inverses
from grounding import provenance
from model import PHASES, Fact, Step
from placeholders import Grounder

MIN_STEPS, MAX_STEPS = 5, 25
MAX_FIELD_CHARS = 400
READ_PHASES = ("triage", "diagnosis", "verify", "escalate", "after")
MISSING_ROLLBACK = "changes state but has no rollback"


@dataclass(frozen=True)
class Linted:
    steps: tuple[Step, ...]
    failures: tuple[tuple[int, str], ...] = ()   # (step index in `steps`, reason)
    dropped: int = 0
    replaced: int = 0


def lint(raw_steps: list, facts: tuple[Fact, ...], source: str) -> Linted:
    by_id = {f.id: f for f in facts}
    grounder = Grounder(source)
    steps: list[Step] = []
    dropped = 0
    for raw in raw_steps or ():
        step = _step(raw, by_id, grounder, source)
        if step is None:
            dropped += 1
        else:
            steps.append(step)
    ordered = order(steps)
    if len(ordered) > MAX_STEPS:
        ordered, cut = _trim(ordered)
        dropped += cut
    return Linted(ordered, tuple(_failures(ordered)), dropped, grounder.replaced)


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    return str(value).strip()[:MAX_FIELD_CHARS] if value not in (None, "") else ""


def _step(raw: object, facts: dict[str, Fact], grounder: Grounder, source: str) -> Step | None:
    if not isinstance(raw, dict) or not _text(raw, "title"):
        return None
    command = _text(raw, "command").strip("`").removeprefix("$ ").strip() or None
    fact_ids = tuple(str(i) for i in raw.get("source_fact_ids") or () if str(i) in facts)
    origin = provenance(command, fact_ids, facts, source)
    phase = _text(raw, "phase").casefold()
    phase = phase if phase in PHASES else "diagnosis"
    kind, review = _kind(command, phase, _text(raw, "kind_hint").casefold())
    if kind == commands.DESTRUCTIVE and origin != "input":
        return None  # a suggested destructive step is never shown
    rollback = _text(raw, "rollback") or None
    if kind == commands.DESTRUCTIVE:
        rollback, phase = inverses.NO_ROLLBACK, "mitigation"
    elif kind == commands.CHANGES_STATE:
        phase = "mitigation" if phase in ("triage", "diagnosis") else phase
        if not inverses.has_real_rollback(rollback):
            rollback = inverses.inverse(command or "", _text(raw, "action")) or None
    g = grounder.ground
    return Step(phase=phase, title=g(_text(raw, "title")), action=g(_text(raw, "action")), kind=kind,
                command=g(command) if command else None, expect=g(_text(raw, "expect")),
                if_not=g(_text(raw, "if_not")), rollback=g(rollback) if rollback else None, source=origin,
                fact_ids=fact_ids, review=review)


def _kind(command: str | None, phase: str, hint: str) -> tuple[str, bool]:
    if command:
        found = commands.classify(command)
        return found.kind, found.review
    if phase in ("escalate", "after"):  # a page or a note: nothing runs against the system
        return commands.READ_ONLY, False
    if phase in READ_PHASES and hint != commands.CHANGES_STATE:
        return commands.READ_ONLY, False
    return commands.CHANGES_STATE, False  # a mitigation with no command: fail closed


def order(steps: list[Step] | tuple[Step, ...]) -> tuple[Step, ...]:
    """Phases in fixed order; mitigation least disruptive first; destructive last; numbered 1..n."""
    def key(item: tuple[int, Step]) -> tuple[int, int, int]:
        index, step = item
        rank = 0
        if step.phase == "mitigation" and step.kind != commands.READ_ONLY:
            text = f"{step.command or ''} {step.title} {step.action}"
            rank = inverses.rank(text, step.kind == commands.DESTRUCTIVE)
        return PHASES.index(step.phase), rank, index
    ordered = [s for _, s in sorted(enumerate(steps), key=key)]
    return tuple(s.with_(n=i) for i, s in enumerate(ordered, start=1))


def _trim(steps: tuple[Step, ...]) -> tuple[tuple[Step, ...], int]:
    keep = list(steps)
    while len(keep) > MAX_STEPS:
        victim = next((s for s in reversed(keep) if s.source != "input"), keep[-1])
        keep.remove(victim)
    return order(keep), len(steps) - len(keep)


def _failures(steps: tuple[Step, ...]) -> list[tuple[int, str]]:
    failures = [(i, MISSING_ROLLBACK) for i, s in enumerate(steps)
                if s.kind == commands.CHANGES_STATE and not inverses.has_real_rollback(s.rollback)]
    if len(steps) < MIN_STEPS:
        failures.append((-1, f"only {len(steps)} usable steps; write {MIN_STEPS} to {MAX_STEPS}"))
    if not any(s.kind == commands.READ_ONLY and s.phase in ("triage", "diagnosis") for s in steps):
        failures.append((-1, "no read-only triage check before the state-changing steps"))
    return failures


def finalize(linted: Linted) -> tuple[tuple[Step, ...], int]:
    """After the repair: a state-changing step still without a rollback becomes an ask-the-owner step."""
    rewritten = 0
    steps = []
    for step in linted.steps:
        if step.kind == commands.CHANGES_STATE and not inverses.has_real_rollback(step.rollback):
            step = step.with_(title=inverses.ASK_OWNER + step.title, rollback=inverses.NO_ROLLBACK)
            rewritten += 1
        steps.append(step)
    return order(steps), rewritten


def structural_failures(linted: Linted) -> bool:
    return any(index == -1 for index, _ in linted.failures)
