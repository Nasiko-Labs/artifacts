"""Category briefing agent: a CrewAI crew behind A2A. No model call at start."""

import logging
import os

logging.basicConfig(level=logging.INFO)

# CrewAI's defaults (telemetry off, storage under /tmp) must be in place before it is imported.
from crewai_env import apply_crewai_env_defaults

apply_crewai_env_defaults()

# Telemetry must initialize before a2a-sdk and Starlette are imported (see the
# platform template oss/agents/langgraph/src/__main__.py for why).
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "category-briefing"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

import card
from agent_executor import BriefingExecutor

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8000


def build_agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        name=card.AGENT_NAME,
        description=card.AGENT_DESCRIPTION,
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://{host}:{port}/")],
        version="1.0.0",
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


def build_app(executor: BriefingExecutor, agent_card: AgentCard):
    # Stateless agent: nothing pauses, so an in-memory task store is enough.
    handler = DefaultRequestHandler(
        agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=agent_card,
    )
    routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/")
    return TraceparentMiddleware(Starlette(routes=routes))


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=DEFAULT_PORT, type=int)
def main(host: str, port: int) -> None:
    app = build_app(BriefingExecutor(), build_agent_card(host, port))
    logger.info("Category briefing agent listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
