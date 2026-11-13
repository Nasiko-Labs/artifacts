"""Escalation summarizer (stateless): the summary, the explicit ask and the stated customer impact of an escalation
thread the user pastes, each statement backed by a verbatim quote that code finds in that thread.

It reads only what the user sends: no tools, no gateway, no other agent. Nothing here calls a model at
startup. Stateless: no checkpointer, an in-memory task store that lives only for the request, never a
pause; earlier turns come from the chat history the platform prepends.
"""

import logging
import os

logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk, Starlette and the OpenAI client are imported, so the httpx and
# OpenAI instrumentors patch the classes before anything holds a reference to them.
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "escalation-summarizer-stateless"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from langchain_openai import ChatOpenAI
from starlette.applications import Starlette

import card
from agent_executor import EscalationExecutor
from chain import EscalationAgent

logger = logging.getLogger(__name__)

DEFAULT_BIND_PORT = 8000
DEFAULT_MODEL = "gpt-4o-mini"
INPUT_MODES = ["text/plain", "text/markdown", "text/csv", "application/json", "text/vtt"]


def build_agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        name=card.AGENT_NAME,
        description=card.AGENT_DESCRIPTION,
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://{host}:{port}/")],
        version=card.AGENT_VERSION,
        default_input_modes=INPUT_MODES,
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[AgentSkill(
            id=card.SKILL_ID, name=card.SKILL_NAME, description=card.SKILL_DESCRIPTION,
            tags=card.SKILL_TAGS, examples=card.SKILL_EXAMPLES, input_modes=INPUT_MODES,
            output_modes=["text/plain", "application/json"],
        )],
    )


def build_app(executor: EscalationExecutor, host: str, port: int, task_store=None):
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
    # The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY. Constructing the client makes no call.
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or DEFAULT_MODEL
    llm = ChatOpenAI(model=model, api_key=os.environ.get("OPENAI_API_KEY") or "unset",
                     base_url=os.environ.get("OPENAI_BASE_URL"), max_retries=0)
    executor = EscalationExecutor(EscalationAgent(), llm, model)

    logger.info("Escalation summarizer (stateless) listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
