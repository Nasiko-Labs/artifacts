"""The chunker-merger turn as a LangChain LCEL pipeline (code only, zero model calls, zero network), stateless.

    intake (classify; a plain paste after an unfinished request completes it) | run (split | a piece | merge |
    follow-up; every split verified losslessly)

Nothing is stored: the pipeline's input is this turn's message only, and follow-ups ("show part 3", "split
again with 10,000 chars", "merge with this one") recompute from the input found in the platform's history slice
(recall.py), so any replica can answer any message. Nothing ever pauses: a missing text or result, or a text
over the cap, gets a plain reply."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.runnables import Runnable, RunnableLambda

import actions
import followup
import recall
import replies
from classify import Request, classify
from envelope import extract_values
from inputs import Incoming

FOLLOW_UPS = frozenset({"show_part", "list_parts", "as_json", "resplit", "merge_more", "merge_notes"})


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


@dataclass(frozen=True)
class _Classified:
    incoming: Incoming
    request: Request


def _intake(incoming: Incoming) -> _Classified:
    previous = recall.previous_request(incoming.history)
    return _Classified(incoming, recall.resolve(classify(incoming.text), incoming.text, previous))


def _run_step(classified: _Classified) -> TurnResult:
    out = _run(classified.request, classified.incoming)
    return TurnResult(_with_notes(out.get("reply") or "", classified.incoming.notes), out.get("envelope"))


def build_pipeline() -> Runnable:
    """Incoming -> TurnResult. Pure functions composed with LCEL; no memory, no model."""
    return RunnableLambda(_intake, name="intake") | RunnableLambda(_run_step, name="run")


class ChunkerAgent:
    """Holds nothing between turns (the pipeline is stateless and shared)."""

    def __init__(self) -> None:
        self._pipeline = build_pipeline()

    async def turn(self, incoming: Incoming, context_id: str) -> TurnResult:
        """`context_id` is only for logs and spans; no state is keyed by it."""
        return await self._pipeline.ainvoke(incoming)


def _run(request: Request, incoming: Incoming) -> dict:
    if request.kind == "intro":
        return {"reply": request.reply}
    if request.kind in FOLLOW_UPS:
        return followup.handle(request, recall.recall(incoming.history), bool(incoming.history))
    if request.kind == "split":
        return actions.split_request(request.payload, request.opts)
    if request.kind in ("append", "end"):
        return actions.piece(request.payload, request.opts, last=request.kind == "end")
    if request.kind == "merge":
        return actions.merge_values(extract_values(request.payload))
    return {"reply": replies.OFF_TOPIC_REPLY}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
