"""One guide request, end to end:

    no traceparent → fixed reply (zero calls)
    intake (code + one LLM call) → off-topic? decline : no repo? ask (free text) or pasted README
      → discover → no code host? pasted README or the paste hint
      → gather (listing, files, ticket, keywords, locate, prs, kb) → assemble (writers + checks) → render

Follow-ups ("commands only", "shorter", "who owns <path>", "focus on <path>", "add a section on <x>")
never reach here: graph.py answers them from the stored guide."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import assemble
import gather
import intake
import plan
import prompts
import render
import sections
import smalltalk
from deps import TurnDeps
from discovery import discover
from guide import Guide
from intake import Request
from reader import BudgetExceeded, Reader, TurnBudget, ask_once
from verify import normalise_text

log = logging.getLogger("onboarding.pipeline")


@dataclass(frozen=True)
class Pause:
    kind: str          # repo
    text: str
    asked: str = ""


@dataclass(frozen=True)
class Outcome:
    reply: str
    guide: Guide | None = None
    pause: Pause | None = None
    envelope: dict | None = None


async def build(deps: TurnDeps, text: str, budget: TurnBudget, *, asked_repo: bool = False) -> Outcome:
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    req = intake.parse_request(text)
    req, off_topic = await _intake(deps, budget, req, text)
    if off_topic:
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    if not req.repo:
        if req.readme_text:
            return await _pasted(deps, budget, req, "no repository named")
        if asked_repo:
            return Outcome(prompts.REPO_STILL_MISSING)
        return Outcome("", pause=Pause("repo", prompts.REPO_QUESTION, text))
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return await _pasted(deps, budget, req, "code host not reachable") if req.readme_text else Outcome(failure)
    reader = Reader.over(deps, catalog, budget)
    if not reader.has_source("code"):
        return await _pasted(deps, budget, req, "no code host connected") if req.readme_text else \
            Outcome(prompts.NO_CODE_HOST)
    got = await gather.collect(deps, reader, req)
    if not got.listed:
        reason = "; ".join(got.coverage) or prompts.NOT_READABLE
        return Outcome(f"I couldn't read the repository {req.repo} ({reason}). Check the name and that this "
                       f"agent's code host connection can see it.\n\n{prompts.PASTE_HINT}")
    guide = await assemble.assemble(deps, budget, req, got, reader.fenced.seen_text)
    log.info("guide built: %d tool calls, %d tool-less LLM calls", budget.tool_calls, budget.llm_calls)
    return finish(guide, req)


def finish(guide: Guide, req: Request) -> Outcome:
    markdown = render.render(guide)
    reply = render.render_owners(guide, req.owners_path) if req.owners_path else markdown
    if req.owners_path:
        reply += "\n\nSay \"show the full guide\" for the whole walkthrough."
    return Outcome(reply, guide, envelope=render.envelope(guide, markdown))


async def _intake(deps: TurnDeps, budget: TurnBudget, req: Request, text: str) -> tuple[Request, bool]:
    messages = ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                {"role": "user", "content": prompts.fence_input(text)})
    try:
        answer = await ask_once(deps, messages, budget)
    except BudgetExceeded:
        return req, False
    return intake.merge_model(req, answer, text)


async def _pasted(deps: TurnDeps, budget: TurnBudget, req: Request, why: str) -> Outcome:
    """No code host: a partial guide from the pasted README alone (confidence low)."""
    readme = req.readme_text
    seen = normalise_text(f"{readme}\n{req.ticket_text}")
    welcome, dropped = await sections.welcome(deps, budget, "", readme, seen)
    groups = plan.commands({"pasted README": readme})
    guide = Guide(repo=req.repo, hire=req.hire, role=req.role, mode="pasted", welcome=welcome,
                  commands=tuple({"source": g.source, "heading": g.heading, "lines": list(g.lines)} for g in groups),
                  checklist=plan.checklist(req.repo or "the repository", groups, (), False, ()),
                  coverage=(f"built from pasted text only ({why}); map, owners, PRs and docs not read",),
                  read={"readme": True}, dropped=dropped)
    return finish(assemble.checked(guide, seen), req)
