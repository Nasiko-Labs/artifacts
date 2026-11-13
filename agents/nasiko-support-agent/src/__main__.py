"""Nasiko support agent: answers how-to and troubleshooting questions about Nasiko from its bundled
notes (docs.nasiko.com, `nasiko --help` and the UI flows). It reads no user data and changes nothing.

Nothing here calls a model at startup. The agent uses no tools, so no tool gateway client exists.
"""

import logging
import os

logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk and Starlette are imported, so their HTTP clients and
# servers are instrumented.
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "nasiko-support-agent"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from openai import AsyncOpenAI
from starlette.applications import Starlette

import card
from agent import SupportAgent
from agent_executor import SupportExecutor

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


def build_app(executor: SupportExecutor, host: str, port: int, task_store=None):
    """Every task completes in one turn (nothing pauses), so an in-memory task store is enough."""
    agent_card = build_agent_card(host, port)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=task_store or InMemoryTaskStore(),
                                    agent_card=agent_card)
    routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/")
    # Outermost, so it records each request's traceparent before a2a-sdk runs execute().
    return TraceparentMiddleware(Starlette(routes=routes))


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=int(os.environ.get("PORT") or DEFAULT_BIND_PORT), type=int)
def main(host: str, port: int) -> None:
    # The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY. Constructing the client makes no call.
    llm = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "unset", base_url=os.environ.get("OPENAI_BASE_URL"))
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or DEFAULT_MODEL
    executor = SupportExecutor(SupportAgent(), llm, model)

    logger.info("Nasiko support agent listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
