"""One request: list the gateway's tools, refuse without them, else run the crew and check it."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from async_bridge import LoopBridge
from briefing import format_briefing, parse_editor_output, post_check
from crew import LlmSettings, build_crew, build_llm
from gateway_tools import ToolSession, build_gateway_tools
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import bound_traceparent

log = logging.getLogger("category_briefing")

REFUSAL_TEXT = (
    "Web search is not connected for this agent, so I can't tell you what the market is reacting "
    "to this week. I won't answer from memory. Ask an admin to grant a search server to this agent "
    "on MCP servers."
)
NO_TRACEPARENT_TEXT = (
    "This request arrived without a traceparent header, so the platform cannot authorize my tool "
    "or model calls. Send it through Nasiko."
)
BROKEN_FLOW_TEXT = "The platform refused my tool calls for this request ({error}). No briefing was produced."
TIMEOUT_TEXT = "The research took longer than {secs:.0f} seconds, so I stopped. No briefing was produced."
# Below the platform's 600 s agent-call and flow timeouts, so this agent answers first.
CREW_DEADLINE_SECS = 540.0


@dataclass(frozen=True)
class PulseResult:
    text: str
    completed: bool


async def market_pulse(
    query: str,
    traceparent: str | None,
    gateway: NasikoMcpClient | None,
    settings: LlmSettings,
    today: date | None = None,
    deadline_secs: float = CREW_DEADLINE_SECS,
) -> PulseResult:
    if not traceparent:
        return PulseResult(NO_TRACEPARENT_TEXT, completed=False)
    if gateway is None:
        return PulseResult(REFUSAL_TEXT, completed=True)
    mcp_tools = await gateway.list_tools(traceparent)
    if not mcp_tools:
        return PulseResult(REFUSAL_TEXT, completed=True)

    session = ToolSession(gateway=gateway, traceparent=traceparent, bridge=LoopBridge(asyncio.get_running_loop()))
    tools = build_gateway_tools(mcp_tools, session)
    if not tools:
        return PulseResult(REFUSAL_TEXT, completed=True)
    crew = build_crew(build_llm(settings, traceparent), tools, query, today or date.today())
    try:
        with bound_traceparent(traceparent):
            output = await asyncio.wait_for(crew.kickoff_async(), timeout=deadline_secs)
    except TimeoutError:
        return PulseResult(TIMEOUT_TEXT.format(secs=deadline_secs), completed=False)
    finally:
        # The worker thread cannot be cancelled; this makes its remaining tool calls no-ops.
        session.stop.set()
    if session.fatal:
        return PulseResult(BROKEN_FLOW_TEXT.format(error=session.fatal[0]), completed=False)
    log.info("crew finished; gateway calls: %s", session.calls)
    checked = post_check(parse_editor_output(output.raw), "\n".join(session.evidence))
    return PulseResult(format_briefing(checked), completed=True)
