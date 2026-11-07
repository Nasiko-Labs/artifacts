"""Research agent: a company's public GitHub footprint via GitHub tools on the Nasiko MCP gateway."""

import logging
import os

logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk and Starlette are imported (see the
# platform template oss/agents/langgraph/src/__main__.py for why).
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "research-agent"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from openai import AsyncOpenAI
from starlette.applications import Starlette

import card
from agent_executor import ResearchExecutor
from nasiko_mcp import NasikoMcpClient

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8000
DEFAULT_MODEL = "gpt-4o-mini"
VERSION = "1.1.0"


def build_agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        name=card.AGENT_NAME,
        description=card.AGENT_DESCRIPTION,
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=f"http://{host}:{port}/"),
        ],
        version=VERSION,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id=card.SKILL_ID,
                name=card.SKILL_NAME,
                description=card.SKILL_DESCRIPTION,
                tags=card.SKILL_TAGS,
                examples=card.SKILL_EXAMPLES,
            ),
        ],
    )


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=DEFAULT_PORT, type=int)
def main(host: str, port: int) -> None:
    # The platform's LLM router injects OPENAI_BASE_URL and OPENAI_API_KEY; the gateway
    # MCP_GATEWAY_URL and MCP_GATEWAY_TOKEN. Constructing clients makes no network call.
    llm = AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )
    model = os.environ.get("MODEL", os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    gateway = NasikoMcpClient.from_env()
    if gateway is None:
        logger.warning("MCP_GATEWAY_URL/MCP_GATEWAY_TOKEN not set: every request answers 'not connected'")
    agent_card = build_agent_card(host, port)

    handler = DefaultRequestHandler(
        agent_executor=ResearchExecutor(llm, model, gateway),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/")
    app = TraceparentMiddleware(Starlette(routes=routes))

    logger.info("Research agent listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
