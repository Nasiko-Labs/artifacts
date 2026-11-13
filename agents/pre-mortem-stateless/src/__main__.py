"""Pre-mortem agent: assumes the pasted plan already failed and writes the most likely failure stories, each
anchored on a line of the plan that code finds verbatim, ranked by likelihood x impact in code.

It reads only what the user sends: no tools, no gateway, no other agent. Nothing here calls a model at
startup.

Built on CrewAI: one crew kickoff per turn (failure-mode finder -> skeptical reviewer).
Stateless: no crew memory, no durable task store and no platform state API; earlier turns come only from
the history the platform prepends to each message, so any replica can answer any message.
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

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "pre-mortem-stateless"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

import card
from agent import PreMortemAgent
from agent_executor import PreMortemExecutor
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


def build_app(executor: PreMortemExecutor, host: str, port: int, task_store=None):
    """`task_store` defaults to an in-memory store: a task lives only for the request that ends it, since
    nothing ever pauses."""
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
    executor = PreMortemExecutor(PreMortemAgent(), LlmSettings.from_env())

    logger.info("Pre-mortem agent listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
