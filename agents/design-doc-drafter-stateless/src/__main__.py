"""Design doc drafter (stateless): turns pasted requirements into a design document with a fixed, code-assembled
structure; the model proposes the design, code checks ids, coverage, numbers and technologies.

It reads only what the user sends: no tools, no gateway, no other agent. Nothing here calls a model at
startup. Built on CrewAI: one crew kickoff per turn (requirements analyst -> software architect ->
technical writer). Stateless: no crew memory, an in-memory task store that lives only for the request, never a
pause; earlier turns come from the chat history the platform prepends.
"""

import logging
import os

logging.basicConfig(level=logging.INFO)

# CrewAI reads its telemetry/tracing/storage env at import: set the defaults before anything imports it.
from crewai_env import apply_crewai_env_defaults

apply_crewai_env_defaults()

# Telemetry must initialize before a2a-sdk and Starlette are imported (the platform's agent template explains
# why: the instrumentors patch those modules at import).
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "design-doc-drafter-stateless"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

import card
from agent import DesignDocAgent
from agent_executor import DesignDocExecutor
from llm import LlmSettings

logger = logging.getLogger(__name__)

DEFAULT_BIND_PORT = 8000
INPUT_MODES = ["text/plain", "text/markdown", "application/json"]


def build_agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        name=card.AGENT_NAME,
        description=card.AGENT_DESCRIPTION,
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://{host}:{port}/")],
        version=card.AGENT_VERSION,
        default_input_modes=INPUT_MODES,
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[AgentSkill(
            id=card.SKILL_ID, name=card.SKILL_NAME, description=card.SKILL_DESCRIPTION,
            tags=card.SKILL_TAGS, examples=card.SKILL_EXAMPLES, input_modes=INPUT_MODES,
            output_modes=["text/plain"],
        )],
    )


def build_app(executor: DesignDocExecutor, host: str, port: int, task_store=None):
    """Tasks are kept in memory only: every task completes within its own request, so nothing needs to
    outlive it or be shared with another replica."""
    agent_card = build_agent_card(host, port)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=task_store or InMemoryTaskStore(),
                                    agent_card=agent_card)
    routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/")
    app = Starlette(routes=routes)
    # Outermost, so it records each request's traceparent before a2a-sdk runs execute().
    return TraceparentMiddleware(app)


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=DEFAULT_BIND_PORT, type=int)
def main(host: str, port: int) -> None:
    # The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY; the crew's LLM is built per request with them.
    executor = DesignDocExecutor(DesignDocAgent(), LlmSettings.from_env())

    logger.info("Design doc drafter (stateless) listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
