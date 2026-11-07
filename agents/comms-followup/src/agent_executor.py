"""A2A executor: wires the A2A task lifecycle to `conversation_loop`.

The plumbing (credential + traceparent extraction, INPUT_REQUIRED /
AUTH_REQUIRED pause and resume through Task.metadata) is carried over from
general-assistant unchanged in behaviour; see that agent for the history
behind each choice. What's new lives in conversation_loop and below it.
"""

import os
from datetime import UTC, datetime

import httpx
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict
from openai import AsyncOpenAI

import conversation_loop as loop
from mcp_client import McpError, call_tool, list_tools
from prompts import SYSTEM_PROMPT
from telemetry import request_otel_context

DEFAULT_MODEL = "gpt-4o"
MCP_HTTP_TIMEOUT_SECS = 60.0

# Direct-chat paths send "{role}: {content}\n...\n\nCurrent message: {text}"
# once a session has prior turns (oss/orchestrator/src/session_history.rs).
# Unlike general-assistant, the history is KEPT: "save that as a Gmail draft"
# arrives as a new task and only makes sense next to the draft from the last turn.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)


def split_session_history(text: str) -> tuple[str | None, str]:
    idx = text.rfind(_CURRENT_MESSAGE_MARKER)
    if idx == -1:
        return None, text
    return text[:idx].strip() or None, text[idx + len(_CURRENT_MESSAGE_MARKER) :]


def build_user_content(raw_text: str) -> str:
    history, current = split_session_history(raw_text)
    if history is None:
        return current
    return (
        "Earlier in this chat session (context only — your previous drafts are here):\n"
        f"{history}\n\nCurrent request: {current}"
    )


class OpenAiChatModel:
    def __init__(self, client: AsyncOpenAI, model: str):
        self._client = client
        self._model = model

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        # tool_choice="required": "paused" vs "done" is a tool call, never a
        # guess about whether free text was meant as a question.
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, tools=tools, tool_choice="required"
        )
        return response.choices[0].message.model_dump(exclude_none=True)


class McpGateway:
    """The Nasiko MCP gateway, bound to this request's credential + traceparent."""

    def __init__(
        self, http: httpx.AsyncClient, url: str | None, token: str | None, traceparent: str | None
    ):
        self._http = http
        self._url = url
        self._token = token
        self._traceparent = traceparent

    async def list_tools(self) -> list[dict]:
        if not (self._url and self._token):
            return []
        return await list_tools(self._http, self._url, self._token, self._traceparent)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if not (self._url and self._token):
            raise McpError("other", "MCP_GATEWAY_URL / MCP_GATEWAY_TOKEN are not configured")
        return await call_tool(self._http, self._url, self._token, name, arguments, self._traceparent)


class CommsFollowupExecutor(AgentExecutor):
    def __init__(self):
        # Placeholder key keeps the agent startable (card, health) without one.
        llm = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY") or "unset",
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
        model = os.environ.get("MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
        self.chat_model = OpenAiChatModel(llm, model)
        # Both injected by the platform at deploy (docs/MCP_GATEWAY_AGENT_AUTH.md).
        self.mcp_gateway_url = os.environ.get("MCP_GATEWAY_URL") or None
        self.mcp_gateway_token = os.environ.get("MCP_GATEWAY_TOKEN") or None
        self.http = httpx.AsyncClient(timeout=MCP_HTTP_TIMEOUT_SECS)

    def _gateway_for(self, context: RequestContext) -> McpGateway:
        token = (context.metadata or {}).get("agent_token") or self.mcp_gateway_token
        return McpGateway(self.http, self.mcp_gateway_url, token, _extract_traceparent(context))

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raw_text = context.get_user_input()
        stored_task = context.current_task
        task = stored_task or new_task_from_user_message(context.message)
        if stored_task is None:
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        gateway = self._gateway_for(context)
        now = datetime.now(UTC)
        stored_state = stored_task.status.state if stored_task is not None else None
        try:
            if stored_state in _PAUSED_STATES:
                # The paused task's metadata round-trips as a protobuf Struct.
                metadata = MessageToDict(stored_task.metadata) if stored_task.metadata else {}
                _, answer = split_session_history(raw_text)
                outcome = await loop.resume(self.chat_model, gateway, metadata, answer, now)
            else:
                state = loop.initial_state(SYSTEM_PROMPT, build_user_content(raw_text))
                outcome = await loop.start(self.chat_model, gateway, state, now)
        except Exception as e:  # surface as a failed task, never a hung one
            print(f"[comms-followup] task {task.id} failed: {e!r}", flush=True)
            await updater.failed(updater.new_agent_message([new_text_part(f"Error: {e}")]))
            return

        await _apply_outcome(updater, outcome)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply_outcome(updater: TaskUpdater, outcome: loop.Outcome) -> None:
    if outcome.kind == "complete":
        await updater.add_artifact([new_text_part(outcome.text)])
        await updater.complete()
        return
    state = (
        TaskState.TASK_STATE_INPUT_REQUIRED
        if outcome.kind == "input_required"
        else TaskState.TASK_STATE_AUTH_REQUIRED
    )
    await updater.update_status(
        state,
        # The HITL keys go on the status message too: the platform reads
        # `status.message.metadata` (or the event's) to build the question and
        # link a gateway approval row by `hitl_request_id`.
        message=updater.new_agent_message([new_text_part(outcome.text)], metadata=outcome.hitl),
        metadata=outcome.metadata or None,
    )


def _extract_traceparent(context: RequestContext) -> str | None:
    """The CURRENT request's traceparent from `call_context.state` — not a
    ContextVar, because a resumed task runs in the worker created by the
    original dispatch (see general-assistant for the full story). The gateway
    rejects tools/call whose trace id isn't a live flow this agent is in."""
    call_context = getattr(context, "call_context", None)
    state = getattr(call_context, "state", None) if call_context is not None else None
    if isinstance(state, dict):
        headers = state.get("headers")
        if isinstance(headers, dict) and headers.get("traceparent"):
            return headers["traceparent"]

    from opentelemetry import trace

    ctx = request_otel_context.get()
    if ctx is None:
        return None
    span_context = trace.get_current_span(ctx).get_span_context()
    if not span_context.is_valid:
        return None
    return (
        f"00-{trace.format_trace_id(span_context.trace_id)}"
        f"-{trace.format_span_id(span_context.span_id)}"
        f"-{span_context.trace_flags:02x}"
    )
