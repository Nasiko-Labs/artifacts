"""Issue-to-PR: turns one small issue into a draft pull request. It localizes the issue (Agentless's cheap
hierarchy, copied from the issue-localizer), lets a sandboxed coding engine (OpenCode, started on first use)
edit only the fetched files, computes the diff in code, and writes to the code host only one approved step
at a time.

The code host is reached only through the tools the Nasiko MCP gateway grants this agent, found per request.
Nothing here calls a model, the gateway or the engine at startup.
"""

import logging
import os

logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk and Starlette are imported (see the platform template
# oss/agents/langgraph/src/__main__.py for why).
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "issue-to-pr"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from openai import AsyncOpenAI
from starlette.applications import Starlette

import card
from agent_executor import IssueToPrExecutor
from engine import OpenCodeEngine
from graph import IssueToPrAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_state import install_shutdown_hooks, make_checkpointer, make_task_store
from tests_runner import TestRunner
from workspace import Workspace

logger = logging.getLogger(__name__)

DEFAULT_BIND_PORT = 8000
DEFAULT_MODEL = "gpt-4o-mini"


def build_agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        name=card.AGENT_NAME,
        description=card.AGENT_DESCRIPTION,
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://{host}:{port}/")],
        version=card.AGENT_VERSION,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[AgentSkill(
            id=card.SKILL_ID, name=card.SKILL_NAME, description=card.SKILL_DESCRIPTION,
            tags=card.SKILL_TAGS, examples=card.SKILL_EXAMPLES,
        )],
    )


def build_app(executor: IssueToPrExecutor, host: str, port: int, task_store=None):
    """`task_store` defaults to the durable platform store, so a paused task survives a restart or
    lands on another replica; tests pass a shared in-memory one."""
    agent_card = build_agent_card(host, port)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=task_store or make_task_store(),
                                    agent_card=agent_card)
    routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/")
    app = Starlette(routes=routes)
    install_shutdown_hooks(app)
    # Outermost, so it records each request's traceparent before a2a-sdk runs execute().
    return TraceparentMiddleware(app)


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=DEFAULT_BIND_PORT, type=int)
def main(host: str, port: int) -> None:
    # The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY; the gateway injects MCP_GATEWAY_URL /
    # MCP_GATEWAY_TOKEN. Constructing these clients makes no network call.
    llm = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "unset", base_url=os.environ.get("OPENAI_BASE_URL"),
                      max_retries=0)
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or DEFAULT_MODEL
    gateway = NasikoMcpClient.from_env()
    if gateway is None:
        logger.warning("no MCP gateway injected: only pasted issues and files can be worked on (diff only)")
    workspace = Workspace()
    if workspace.available():
        logger.info("swept %d stale run directories", workspace.sweep())
    else:
        logger.warning("no writable /workspace: the coding engine cannot run (deploy with --writable)")
    executor = IssueToPrExecutor(IssueToPrAgent(make_checkpointer()), llm, model, gateway,
                                 engine=OpenCodeEngine(model=model), workspace=workspace, runner=TestRunner())

    logger.info("Issue-to-PR listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
