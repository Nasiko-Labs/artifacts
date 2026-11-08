"""The graph's node bodies: narrate a table, follow up on the stored story, or scope-check a message that
came with no table. Each returns the state update; `scope` carries this turn's input and the envelope data
part, which is never persisted."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import facts as facts_mod
import hitl
import llm
import prompts
import render
import smalltalk
from checks import clean_text
from envelope import Parsing, build
from fact import FactSet
from inputs import Incoming
from intake import Plan
from request import Ask, read_ask
from table import build as build_table
from template import narrate as template_narrate
from write import Story, answer_question, write_story

MIN_ROWS = 3
SOURCE_KEEP_CHARS = 20_000      # kept only to allow links that appeared in the user's input


@dataclass
class Scope:
    deps: llm.TurnDeps
    incoming: Incoming
    plan: Plan | None = None
    data: dict | None = None
    notes: list[str] = field(default_factory=list)


def _reply(scope: Scope, envelope: dict, view: str = "default", source: str = "") -> str:
    scope.data = envelope
    if scope.incoming.wants_json or view == "json":
        return render.as_json(envelope)
    return render.render(envelope, view, source, extra_notes=scope.notes)


def _facts(scope: Scope) -> tuple[FactSet | None, Parsing, str]:
    source = scope.plan.source
    if source.facts is not None:
        return source.facts, Parsing(), ""
    table = build_table(source.records, source.instruction)
    if table.rows < MIN_ROWS:
        return None, Parsing(), f"{prompts.TOO_FEW_ROWS} {smalltalk.INTRO}"
    found = facts_mod.build(table)
    if not found.facts:
        return None, Parsing(), "\n".join(found.notes)
    return found, Parsing(table.header_guessed, table.unparsed_share()), ""


async def narrate(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    found, parsing, refusal = _facts(scope)
    if found is None:
        return {"reply": refusal}
    ask = read_ask(scope.plan.source.instruction)
    story = await write_story(scope.deps, found, ask, scope.incoming.context)
    if story.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    source = scope.incoming.text[:SOURCE_KEEP_CHARS]
    return _saved(scope, found, story, ask, parsing, source)


def _saved(scope: Scope, found: FactSet, story: Story, ask: Ask, parsing: Parsing, source: str) -> dict:
    envelope = build(found, story, ask, parsing)
    return {"reply": _reply(scope, envelope, source=source), "facts": found.to_dict(), "ask": ask.to_dict(),
            "parsing": parsing.to_dict(), "envelope": envelope, "source": source}


async def follow(state: dict, scope: Scope) -> dict:
    envelope, plan = state.get("envelope"), scope.plan
    if not envelope or not state.get("facts"):
        return {"reply": prompts.NO_TABLE_STORED}
    found, ask = FactSet.from_dict(state["facts"]), Ask.from_dict(state.get("ask"))
    parsing, source = Parsing.from_dict(state.get("parsing")), state.get("source") or ""
    if plan.command in ("headline", "numbers", "json"):
        return {"reply": _reply(scope, envelope, plan.command, source)}
    if plan.command == "longer":
        longer = replace(ask, length="default" if ask.length != "exec" else "exec")
        fallback = template_narrate(found, longer)
        story = Story("ok", fallback.headline, fallback.body, fallback.uses, template="requested")
        return _saved(scope, found, story, longer, parsing, source)
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.command == "rewrite":
        new_ask = read_ask(plan.text, base=ask)
        story = await write_story(scope.deps, found, new_ask, scope.incoming.context)
        if story.status == "off_topic":
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        return _saved(scope, found, story, new_ask, parsing, source)
    answer = await answer_question(scope.deps, found, plan.text, ask.instruction, scope.incoming.context)
    if answer.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    scope.data = {**envelope, "answer": answer.text, "answer_status": answer.status, "answer_uses": list(answer.uses)}
    if scope.incoming.wants_json:
        return {"reply": render.as_json(scope.data)}
    return {"reply": f"{clean_text(answer.text, source)}\n\n_({prompts.FOOTER})_"}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    try:
        raw = await llm.complete(scope.deps, prompts.scope_messages(scope.plan.text, scope.incoming.context))
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    data = llm.load_json(raw)
    if isinstance(data, dict) and data.get("status") == "needs_table":
        return {"pause": hitl.Pending(hitl.NEED_SOURCE, question=scope.plan.text).to_metadata()}
    return {"reply": smalltalk.OFF_TOPIC_REPLY}
