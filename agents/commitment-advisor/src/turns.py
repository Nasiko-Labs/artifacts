"""One fresh run: parse the paste and the options in code, run the pipeline, then one explanation call.
Follow-ups on a stored result live in followup.py."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import commitments
import envelope
import explain
import pipeline
import prompts
import render
import smalltalk
import usage as usage_mod
from inputs import Incoming, Request
from llm import TurnDeps
from params import Params
from params import parse as parse_params

DEFAULT_REQUEST = "Should we buy a commitment, and what should we do with the existing ones?"
MAX_STORED_PASTE_CHARS = 300_000


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    stored: dict | None = None          # {"paste": {...}, "result": {...}} to checkpoint


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def compute(paste: Mapping[str, str], params: Params) -> dict | str:
    """The pipeline result, or the fixed reply explaining why there is none."""
    try:
        usage = usage_mod.parse(paste.get("usage") or "")
    except usage_mod.UsageError:
        return prompts.NOT_USAGE
    text = paste.get("commitments") or ""
    parsed = commitments.parse(text) if text.strip() else commitments.Parsed()
    return pipeline.run(usage, parsed, params)


def storable(paste: Mapping[str, str]) -> dict:
    size = sum(len(v or "") for v in paste.values())
    return dict(paste) if size <= MAX_STORED_PASTE_CHARS else {}


async def analyze(incoming: Incoming, deps: TurnDeps, req: Request) -> TurnResult:
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    paste = {"usage": req.usage_csv, "commitments": req.commitments_csv}
    result = compute(paste, parse_params(req.instruction))
    if isinstance(result, str):
        return reply(result, incoming.notes)
    if result["needs_more_data"]:
        explained = explain.with_template(result)
    else:
        explained = await explain.explain(deps, result, req.instruction or DEFAULT_REQUEST, incoming.context)
        if explained is None:
            return reply(smalltalk.OFF_TOPIC_REPLY)
    return finish(explained, incoming, stored={"paste": storable(paste), "result": explained})


def finish(result: Mapping, incoming: Incoming, stored: dict | None = None, notes: tuple[str, ...] = ()
           ) -> TurnResult:
    data = envelope.build(result)
    if incoming.wants_json:
        return TurnResult(render.as_json(data), data=data, stored=stored)
    text = render.render(result, (*notes, *incoming.notes))
    if result.get("needs_more_data"):
        text = prompts.NEEDS_MORE_DATA.format(days=result["coverage"]["days"]) + "\n\n" + text
    return TurnResult(text, data=data, stored=stored)
