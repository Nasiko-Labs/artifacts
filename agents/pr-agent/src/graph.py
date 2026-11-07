"""The pr-agent LangGraph: read a PR through the gateway, run one PR-Agent-style command on its
compressed diff, and post the result to GitHub only after a human confirms.

    START → understand ─┬─ (no PR / nothing to post / bare /ask) ─────────────────→ END
                        ├─ (new command) → analyze ─┬─ (no post asked) ─────────────→ END
                        │                           └─ (post asked) ─┐
                        └─ ("post it": last result) ─────────────────┴→ confirm_post (interrupt)
                                                                             ↓
                                    post ─┬─ (posted / declined / failed) → END
                                          └─ (gateway asks approval) → await_post_approval
                                                (interrupt) → resume_post ─┬→ END
                                                                           └→ await_post_approval

State is checkpointed per A2A contextId (thread_id), so a follow-up ("/ask why ...", "post it")
can refer to the PR and the result of the turn before. The graph is compiled per turn because its
nodes close over that turn's dependencies (LLM client, gateway, inbound traceparent); the
checkpointer is shared. Pause nodes hold only `interrupt(...)`: a node re-runs from its start on resume.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command as Resume
from langgraph.types import interrupt

import prompts
from commands import PrRef, is_post_confirmation, parse_request
from diff import DEFAULT_DIFF_TOKEN_BUDGET, CompressedDiff, compress, extract_files, render_for_model
from fence import FencedGateway
from nasiko_hitl import NoHitlRequestError, approval_pause_metadata, question_metadata
from nasiko_mcp import ApprovalPending, AuthorizationRequired, McpGatewayError, McpTool, NasikoMcpClient, ToolDenied, \
    ToolOk
from post_guard import PostGuard
from pr_info import PrInfo, extract_pr_info
from report import comment_body, load_json_object, render
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("pr_agent.graph")

FETCH_MAX_ITERATIONS = 10
POST_MAX_ITERATIONS = 5
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class TurnDeps:
    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    diff_budget: int = DEFAULT_DIFF_TOKEN_BUDGET


@dataclass(frozen=True)
class TurnOutcome:
    """A finished turn (`reply`) or a pause (`pause` = {"text", "metadata"} for input-required)."""

    reply: str
    pause: Mapping[str, Any] | None = None


class PrState(TypedDict, total=False):
    query: str
    pr: dict | None  # the PR this context is about
    command: str
    question: str
    wants_post: bool
    report: str  # the last result, what "post it" posts
    report_pr: dict | None
    post_answer: str
    pending: dict | None  # paused post: {"hitl_request_id", "loop"}
    reply: str
    route: str


class _NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


class PrAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def answer(self, query: str, context_id: str, deps: TurnDeps) -> TurnOutcome:
        turn = {"query": query, "reply": "", "route": "", "post_answer": ""}
        return await self._run(turn, context_id, deps)

    async def resume(self, answer: str, context_id: str, deps: TurnDeps) -> TurnOutcome | None:
        """Continue a paused turn with the human's answer; None when there is nothing to resume."""
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        snapshot = await graph.aget_state(_config(context_id))
        if not snapshot.next:
            return None
        return await self._run(Resume(resume=answer), context_id, deps)

    async def _run(self, payload: Any, context_id: str, deps: TurnDeps) -> TurnOutcome:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        final = await graph.ainvoke(payload, _config(context_id), durability="exit")
        interrupts = final.get("__interrupt__") or ()
        if interrupts:
            value = interrupts[0].value
            return TurnOutcome(reply="", pause={"text": value["text"], "metadata": value.get("metadata")})
        return TurnOutcome(reply=final.get("reply") or prompts.UNREADABLE_ANSWER)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def analyze(state: PrState) -> dict:
        return await _analyze(state, deps)

    async def post(state: PrState) -> dict:
        return await _post(state, deps)

    async def resume_post(state: PrState) -> dict:
        return await _resume_post(state, deps)

    graph = StateGraph(PrState)
    graph.add_node("understand", _understand)
    graph.add_node("analyze", analyze)
    graph.add_node("confirm_post", _confirm_post)
    graph.add_node("post", post)
    graph.add_node("await_post_approval", _await_post_approval)
    graph.add_node("resume_post", resume_post)
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", _route, ["analyze", "confirm_post", END])
    graph.add_conditional_edges("analyze", _route, ["confirm_post", END])
    graph.add_edge("confirm_post", "post")
    graph.add_conditional_edges("post", _route, ["await_post_approval", END])
    graph.add_edge("await_post_approval", "resume_post")
    graph.add_conditional_edges("resume_post", _route, ["await_post_approval", END])
    return graph


def _route(state: PrState) -> str:
    return state.get("route") or END


# --- understand ---------------------------------------------------------------------------------

async def _understand(state: PrState) -> dict:
    request = parse_request(state["query"])
    if request.post_only:
        if not state.get("report"):
            return _finish(prompts.NOTHING_TO_POST)
        return {"route": "confirm_post", "pending": None}
    pr = request.pr or PrRef.from_state(state.get("pr"))
    if pr is None:
        return _finish(prompts.NO_PR_REF)
    if request.command == "ask" and not request.question:
        return {**_finish(f"What's your question about {pr.slug}? Send `/ask <question>`."), "pr": pr.to_state()}
    return {"route": "analyze", "pr": pr.to_state(), "command": request.command, "question": request.question,
            "wants_post": request.wants_post, "pending": None}


# --- analyze ------------------------------------------------------------------------------------

async def _analyze(state: PrState, deps: TurnDeps) -> dict:
    pr = PrRef.from_state(state["pr"])
    fetched, failure = await _fetch(pr, deps)
    if failure is not None:
        return _finish(failure)
    files, info = fetched
    diff = compress(files, deps.diff_budget)
    if not diff.included:
        return _finish(f"{pr.slug} changes no file I can analyze. {diff.coverage_note()}".strip())
    report = await _run_command(state, deps, pr, info, diff)
    if report is None:
        return _finish(prompts.UNREADABLE_ANSWER)
    update = {"report": report, "report_pr": pr.to_state()}
    if state.get("wants_post"):
        return {**update, "route": "confirm_post"}
    return {**update, **_finish(report)}


async def _fetch(pr: PrRef, deps: TurnDeps) -> tuple[tuple[tuple, PrInfo] | None, str | None]:
    """The PR's changed files and metadata, read from this turn's tool output; or the fixed reply."""
    tools, failure = await _discover_tools(deps)
    if failure is not None:
        return None, failure
    fenced = FencedGateway(deps.gateway)
    messages = ({"role": "system", "content": prompts.fetch_system(pr)},
                {"role": "user", "content": f"Fetch {pr.url} and its changed files with patches."})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                 max_iterations=FETCH_MAX_ITERATIONS)
    if result.status == "approval_pending":
        return None, prompts.APPROVAL_NOT_SUPPORTED
    if result.status == "authorization_required":
        return None, prompts.AUTHORIZATION_NEEDED.format(message=result.text)
    files = extract_files(fenced.raw_texts)
    answer = load_json_object(result.text) or {}
    if not files:
        return None, _no_files_reply(pr, answer.get("status"), result)
    info = extract_pr_info(fenced.raw_texts).merged_with(PrInfo.from_mapping(answer))
    log.info("fetched %s: %d changed file(s), tools called %s", pr.slug, len(files), list(result.tools_called))
    return (files, info), None


def _no_files_reply(pr: PrRef, status: Any, result: LoopResult) -> str:
    if status == "no_github_tool":
        return prompts.NO_GITHUB_TOOL
    if status == "not_found":
        return prompts.PR_NOT_FOUND.format(slug=pr.slug)
    if result.status == "max_iterations":
        return result.text
    return prompts.NO_FILES.format(slug=pr.slug)


async def _discover_tools(deps: TurnDeps) -> tuple[tuple[McpTool, ...], str | None]:
    """The gateway catalog for this request, or the fixed reply that ends the turn without an LLM call."""
    if deps.gateway is None:
        return (), prompts.NO_GITHUB_REFUSAL
    if not deps.traceparent:
        return (), prompts.NO_TRACE_CONTEXT
    try:
        tools = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return (), prompts.GATEWAY_UNREACHABLE
    if not tools:
        return (), prompts.NO_GITHUB_REFUSAL
    log.info("tools offered to the model: %s", [tool.name for tool in tools])
    return tools, None


async def _run_command(state: PrState, deps: TurnDeps, pr: PrRef, info: PrInfo, diff: CompressedDiff) -> str | None:
    """One tool-less call for the command, and one repair call if the JSON is unreadable."""
    command, question = state["command"], state.get("question") or ""
    messages = (
        {"role": "system", "content": prompts.SYSTEMS[command]},
        {"role": "user", "content": prompts.analysis_user_message(pr, info, render_for_model(diff), question)},
    )
    first = await _complete(deps, messages)
    rendered = _render(command, first, pr, info, diff, question)
    if rendered is not None:
        return rendered
    log.info("%s answer was not the agreed JSON; asking once for a rewrite", command)
    retry = (*messages, {"role": "assistant", "content": first},
             {"role": "user", "content": prompts.REPAIR_INSTRUCTION})
    return _render(command, await _complete(deps, retry), pr, info, diff, question)


def _render(command: str, text: str, pr: PrRef, info: PrInfo, diff: CompressedDiff, question: str) -> str | None:
    answer = load_json_object(text)
    return None if answer is None else render(command, answer, pr, info, diff, question)


async def _complete(deps: TurnDeps, messages: tuple[dict, ...]) -> str:
    result = await run_tool_loop(deps.llm, deps.model, messages, (), _NoTools(), deps.traceparent, max_iterations=1)
    return result.text


# --- post (human-confirmed) ---------------------------------------------------------------------

async def _confirm_post(state: PrState) -> dict:
    pr = PrRef.from_state(state["report_pr"])
    answer = interrupt({
        "kind": "confirm_post",
        "text": prompts.confirm_text(state["report"], pr),
        "metadata": question_metadata(prompts.POST_QUESTION, [
            (prompts.POST_OPTION, f"Post the result above as one comment on {pr.slug}."),
            (prompts.SKIP_OPTION, "Keep it in this chat only."),
        ], allow_custom_input=False),
    })
    return {"post_answer": str(answer or "")}


async def _post(state: PrState, deps: TurnDeps) -> dict:
    if not is_post_confirmation(state.get("post_answer") or ""):
        return _finish(_with_report(prompts.NOT_POSTED, state))
    pr = PrRef.from_state(state["report_pr"])
    tools, failure = await _discover_tools(deps)
    if failure is not None:
        return _finish(_with_report(failure, state))
    body = comment_body(state["report"])
    guard = PostGuard(deps.gateway, body)
    messages = ({"role": "system", "content": prompts.post_system(pr)},
                {"role": "user", "content": prompts.post_user_message(body)})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, guard, deps.traceparent,
                                 max_iterations=POST_MAX_ITERATIONS)
    return _after_post(state, pr, result, guard)


async def _await_post_approval(state: PrState) -> dict:
    pending = state.get("pending") or {}
    interrupt({"kind": "approval", "text": "Posting the comment needs your approval on the Nasiko card. "
                                           "Approve it to post, or reject it to post nothing.",
               "metadata": {"hitl_request_id": pending.get("hitl_request_id")}})
    return {}


async def _resume_post(state: PrState, deps: TurnDeps) -> dict:
    pr = PrRef.from_state(state["report_pr"])
    tools, failure = await _discover_tools(deps)
    if failure is not None:
        return {"pending": None, **_finish(_with_report(failure, state))}
    guard = PostGuard(deps.gateway, comment_body(state["report"]))
    paused = LoopResult.from_metadata((state.get("pending") or {})["loop"])
    result = await resume_tool_loop(deps.llm, deps.model, paused, tools, guard, deps.traceparent,
                                    max_iterations=POST_MAX_ITERATIONS)
    return _after_post(state, pr, result, guard)


def _after_post(state: PrState, pr: PrRef, result: LoopResult, guard: PostGuard) -> dict:
    if result.status in _PAUSED and result.pending is not None:
        try:
            hitl = approval_pause_metadata(result.pending.result)["hitl_request_id"]
        except NoHitlRequestError:
            return {"pending": None, **_finish(_with_report(prompts.POST_NO_HITL, state))}
        return {"pending": {"hitl_request_id": hitl, "loop": result.to_metadata()}, "route": "await_post_approval"}
    return {"pending": None, **_finish(_with_report(_post_reply(pr, guard, result), state))}


def _post_reply(pr: PrRef, guard: PostGuard, result: LoopResult) -> str:
    outcome = guard.post_outcome
    if guard.posted:
        return prompts.POSTED.format(url=pr.url)
    if isinstance(outcome, ToolDenied):
        return prompts.POST_DENIED.format(message=outcome.message)
    if isinstance(outcome, ToolOk):
        return prompts.POST_FAILED.format(slug=pr.slug, detail=f"GitHub said: {outcome.text[:300]}")
    if isinstance(outcome, (ApprovalPending, AuthorizationRequired)):
        return prompts.POST_FAILED.format(slug=pr.slug, detail=outcome.message)
    return prompts.POST_FAILED.format(slug=pr.slug, detail=f"No comment was sent. {result.text[:300]}".strip())


def _with_report(line: str, state: PrState) -> str:
    return f"{line}\n\n{state['report']}" if state.get("report") else line


# --- shared -------------------------------------------------------------------------------------

def _finish(reply: str) -> dict:
    return {"reply": reply, "route": ""}
