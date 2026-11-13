"""Reply drafter agent: writes your reply to a message you received, in the style measured from your past
replies, using only the facts you and the incoming message give. It drafts only: no data access, no sending.

Nothing here calls a model at startup. The agent uses no tools, so no tool gateway client exists.
Stateless: no checkpointer, no durable task store, no platform state API; a task store lives in memory only
for the request, so any replica can answer any message.
"""

import logging
import os

logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk and Starlette are imported (see the platform template
# oss/agents/langgraph/src/__main__.py for why).
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "reply-drafter-stateless"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from openai import AsyncOpenAI
from starlette.applications import Starlette

import card
from agent_executor import ReplyDrafterExecutor
from graph import ReplyDrafterAgent

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


def build_app(executor: ReplyDrafterExecutor, host: str, port: int):
    """In-memory task store: a task is finished within its own request, so nothing needs to outlive it."""
    agent_card = build_agent_card(host, port)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=agent_card)
    routes = create_agent_card_routes(agent_card) + create_jsonrpc_routes(handler, rpc_url="/")
    app = Starlette(routes=routes)
    # Outermost, so it records each request's traceparent before a2a-sdk runs execute().
    return TraceparentMiddleware(app)


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=DEFAULT_BIND_PORT, type=int)
def main(host: str, port: int) -> None:
    # The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY. Constructing the client makes no call.
    llm = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "unset", base_url=os.environ.get("OPENAI_BASE_URL"))
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or DEFAULT_MODEL
    executor = ReplyDrafterExecutor(ReplyDrafterAgent(), llm, model)

    logger.info("Reply drafter agent (stateless) listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
