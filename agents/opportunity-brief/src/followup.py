"""Follow-ups on the brief stored in this conversation, recognised in code: "drill into theme 2" (one call
over that theme's stored items), "risks of theme 1" / "shorter" / "as json" (re-rendered, zero calls) and
"refresh" (collect again). Anything else goes to intake."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

import brief
import models
import prompts
import render
import write as writing
from deps import TurnDeps
from llm import LlmError
from verify import verify

log = logging.getLogger("opportunity_brief.followup")

_THEME = r"(?:theme|#|number|no\.?)\s*#?(\d{1,2})"
_DRILL = re.compile(r"\b(?:drill|dig|deeper|deep dive|more on|expand|zoom|tell me more)\b.*?" + _THEME, re.IGNORECASE)
_RISKS = re.compile(r"\brisks?\b(?:.*?" + _THEME + r")?", re.IGNORECASE)
_SHORTER = re.compile(r"^(?:please |can you |make it |now )*(?:shorter|short(?:er)? version|tl;?dr|condense|"
                      r"more concise|brief(?:er)? version)\b", re.IGNORECASE)
_REFRESH = re.compile(r"^(?:please |now )?(?:refresh|re-?run|run it again|fetch again|update it)\b", re.IGNORECASE)
_JSON_ONLY = re.compile(r"^(?:(?:give it|show it|return it|send it|same|now|ok)\s+)?(?:as|in) json[.!]?$|^json(?: only)?$",
                        re.IGNORECASE)
_RISKS_ONLY = re.compile(r"^(?:what are |show |list |just |only )*(?:the )?risks?(?: only)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str            # drill | risks | shorter | json | refresh
    theme: int | None = None


def detect(text: str) -> Command | None:
    plain = " ".join((text or "").split())
    if len(plain) > 160:
        return None
    if m := _DRILL.search(plain):
        return Command("drill", int(m.group(1)))
    if _REFRESH.match(plain):
        return Command("refresh")
    if _JSON_ONLY.match(plain):
        return Command("json")
    if _SHORTER.match(plain):
        return Command("shorter")
    m = _RISKS.search(plain)
    if m and (m.group(1) or _RISKS_ONLY.match(plain)):
        return Command("risks", int(m.group(1)) if m.group(1) else None)
    return None


async def handle(deps: TurnDeps, command: Command, state: Mapping, wants_json: bool) -> brief.Outcome:
    env = state.get("envelope")
    if not env:
        return brief.Outcome(prompts.NO_BRIEF_YET)
    if command.kind == "refresh":
        if not deps.traceparent:
            return brief.Outcome(prompts.NO_TRACE_CONTEXT)
        return await brief.run(deps, brief.Spec.from_dict(state["spec"]))
    count = len(env["items"])
    if command.theme is not None and not 1 <= command.theme <= count:
        return brief.Outcome(prompts.NO_SUCH_THEME.format(count=count) if count else prompts.NO_BRIEF_YET)
    if command.kind == "json" or wants_json and command.kind != "drill":
        return brief.Outcome(render.as_json(env), env, dict(state))
    if command.kind == "shorter":
        return brief.Outcome(render.full(env, short=True), env, dict(state))
    if command.kind == "risks":
        themes = [command.theme] if command.theme else list(range(1, count + 1))
        return brief.Outcome("\n\n".join(render.risks(env, n) for n in themes), env, dict(state))
    return await _drill(deps, state, command.theme or 1)


async def _drill(deps: TurnDeps, state: Mapping, n: int) -> brief.Outcome:
    if not deps.traceparent:
        return brief.Outcome(prompts.NO_TRACE_CONTEXT)
    spec, held, clusters, _ = brief.restore(state)
    env = state["envelope"]
    label = env["items"][n - 1]["label"]
    chosen = next((c for c in clusters if c.label == label), None)
    if chosen is None:
        return brief.Outcome(render.drill(env, n, env["items"][n - 1]), env, dict(state))
    by_id = held.by_id()
    items = [by_id[i] for i in chosen.item_ids if i in by_id][:writing.MAX_DRILL_ITEMS]
    themes = [(n, chosen, items)]
    prompt = writing.write_user(spec.topic, spec.angle, spec.window_days, themes, deps.now, writing.DRILL_TEXT_CHARS)
    try:
        drafts = writing.drafts_by_index(await writing.drill(deps, spec.topic, spec.angle, spec.window_days, n,
                                                             chosen, items))
        checked, _ = await verify(deps, drafts, themes, by_id, prompt)
    except LlmError as e:
        log.warning("drill failed at the model: %s", e)
        return brief.Outcome(prompts.FAILED_MODEL)
    final, _ = models.themes(checked, themes, by_id, spec.window_days, deps.now)
    if not final:
        lead = "I couldn't verify enough new evidence to go deeper; here is the theme as it stood.\n\n"
        return brief.Outcome(lead + render.drill(env, n, env["items"][n - 1]), env, dict(state))
    deep = {**final[0], "strength": env["items"][n - 1]["strength"]}
    return brief.Outcome(render.drill(env, n, deep), env, dict(state))
