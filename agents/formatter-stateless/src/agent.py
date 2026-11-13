"""The formatter turn as a LangChain LCEL chain (code only, zero model calls).

    intake ─┬─ render (JSON or Markdown -> the target)
            └─ fixed reply

`TURN` is `RunnableLambda(intake) | RunnableBranch(fixed reply, render)`. It is built once and holds nothing:
every invocation starts from this turn's input alone. "as slack", "as email" or "compact" re-render the input
recalled from the platform's history slice (intake.recall), so any replica can answer any message. Nothing
pauses. No LangChain memory, no chat model: the formatter is deterministic code.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda

import envelope
import intake
import output
from inputs import Incoming


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


@dataclass(frozen=True)
class _Planned:
    incoming: Incoming
    plan: intake.Plan


def _intake(incoming: Incoming) -> _Planned:
    return _Planned(incoming, intake.plan_turn(incoming))


def _is_fixed_reply(step: _Planned) -> bool:
    return step.plan.route == "reply"


def _fixed_reply(step: _Planned) -> TurnResult:
    return TurnResult(_with_notes(step.plan.reply, step.incoming.notes))


def _render(step: _Planned) -> TurnResult:
    plan, incoming = step.plan, step.incoming
    if plan.route == "markdown":
        rendering = envelope.render_markdown(plan.md_source, plan.opts)
    else:
        rendering = envelope.render_values(list(plan.values), plan.opts)
    result = output.reply(rendering, as_json=plan.as_json, plain_version=plan.plain_version)
    return TurnResult(_with_notes(result.text, incoming.notes), result.envelope)


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)


TURN: Runnable[Incoming, TurnResult] = RunnableLambda(_intake) | RunnableBranch(
    (RunnableLambda(_is_fixed_reply), RunnableLambda(_fixed_reply)),
    RunnableLambda(_render),
)


class FormatterAgent:
    """Holds nothing between turns."""

    async def turn(self, incoming: Incoming, context_id: str) -> TurnResult:
        """`context_id` is only for logs and spans; no state is keyed by it."""
        return await TURN.ainvoke(incoming)
