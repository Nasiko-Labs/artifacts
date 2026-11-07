"""One triage pass: rules in code first, whole-thread reads for the ambiguous ones, the model for what is
left, then the order. Also the VIP re-run (rules only, no tool or model call) and the ordering itself."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import classify
import fetch
import prompts
import rules
import signals
from deps import TurnDeps
from models import Checks, Mail, Thread

THREAD_CAP = 60
_FAR_FUTURE = "9999-12-31"


@dataclass(frozen=True)
class Outcome:
    threads: tuple[Thread, ...]
    checks: Checks
    already_answered: int = 0
    mails: Mapping[str, Mail] = field(default_factory=dict)


async def run(deps: TurnDeps, mails: Sequence[Mail], vips: Sequence[str], me: Sequence[str], *,
              reader: fetch.Reader | None = None, notes: Sequence[str] = (), pasted: bool = False,
              capped: bool = False) -> Outcome:
    decided, ambiguous, excluded = _apply_rules(deps, mails, vips, me)
    by_id = {m.id: m for m in mails}
    if reader is not None and ambiguous:
        reads = await fetch.read_threads(deps, reader, sorted(ambiguous, key=lambda m: not m.truncated), me)
        if reads:
            by_id = {**by_id, **reads}
            more, ambiguous, gone = _apply_rules(deps, [reads.get(m.id, m) for m in ambiguous], vips, me)
            decided, excluded = {**decided, **more}, excluded + gone
    pairs = [(m, signals.extract(m, me, vips, deps.now)) for m in ambiguous]
    result = await classify.classify(deps, pairs)
    all_notes = list(notes)
    if result.out_of_time:
        all_notes.append(prompts.TIME_NOTE)
    if reader is not None and reader.budget.left <= 0:
        all_notes.append(prompts.BUDGET_NOTE)
    threads = order([*decided.values(), *result.threads])
    checks = Checks(demoted=result.demoted, dropped=result.dropped, unclassified=result.unclassified,
                    truncated=sum(1 for m in ambiguous if m.truncated), capped=capped, pasted=pasted,
                    notes=tuple(dict.fromkeys(all_notes)))
    return Outcome(tuple(threads), checks, excluded, by_id)


def _apply_rules(deps: TurnDeps, mails: Sequence[Mail], vips: Sequence[str],
                 me: Sequence[str]) -> tuple[dict[str, Thread], list[Mail], int]:
    decided: dict[str, Thread] = {}
    ambiguous: list[Mail] = []
    excluded = 0
    for mail in mails:
        outcome = rules.apply(mail, signals.extract(mail, me, vips, deps.now), deps.today)
        if outcome.excluded:
            excluded += 1
        elif outcome.thread is not None:
            decided[mail.id] = outcome.thread
        else:
            ambiguous.append(mail)
    return decided, ambiguous, excluded


def rerun(deps: TurnDeps, threads: Sequence[Thread], mails: Mapping[str, Mail], vips: Sequence[str],
          me: Sequence[str]) -> tuple[Thread, ...]:
    """The shown threads with the rules re-applied for new VIPs. A thread the rules still leave open keeps its
    earlier bucket; one the user moved keeps the user's choice. No tool or model call."""
    out: list[Thread] = []
    for thread in threads:
        mail = mails.get(thread.id)
        if mail is None or thread.source == "user":
            out.append(thread)
            continue
        sig = signals.extract(mail, me, vips, deps.now)
        decided = rules.apply(mail, sig, deps.today).thread
        out.append(decided if decided is not None and (decided.source == "rule") else
                   rules.make_thread(mail, sig, thread.bucket, thread.reason, thread.evidence, thread.source))
    return tuple(order(out))


def order(threads: Sequence[Thread]) -> list[Thread]:
    """Urgent by deadline then age (oldest first); reply by age (oldest first); ignore newest first."""
    urgent = sorted((t for t in threads if t.bucket == "urgent"),
                    key=lambda t: (t.deadline or _FAR_FUTURE, -(t.age_days or 0.0), t.id))
    reply = sorted((t for t in threads if t.bucket == "reply"), key=lambda t: (-(t.age_days or 0.0), t.id))
    ignore = sorted((t for t in threads if t.bucket == "ignore"), key=lambda t: (t.age_days or 0.0, t.id))
    return [*urgent, *reply, *ignore]
