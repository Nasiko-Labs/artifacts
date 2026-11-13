"""Formatter agent: renders a JSON envelope (or any JSON) as Markdown, Slack Block Kit blocks or email
HTML, in code. Same JSON in, same text out.

No model, no tools, no gateway, no network: nothing here calls anything at startup or per turn.
Stateless: no checkpointer, an in-memory task store that lives only for the request, no platform state API,
so any replica can answer any message.
"""

import logging
import os

logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk and Starlette are imported, so the instrumentors patch the
# classes before anything uses them.
from telemetry import TraceparentMiddleware, init_telemetry

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "formatter-stateless"))

import click
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

import card
from agent import FormatterAgent
from agent_executor import FormatterExecutor

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


def build_app(executor: FormatterExecutor, host: str, port: int, task_store=None):
    """`task_store` defaults to an in-memory store: a task is finished within its own request, so nothing
    needs to outlive it or be shared between replicas."""
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
    executor = FormatterExecutor(FormatterAgent())
    logger.info("Formatter-stateless agent listening on %s:%s", host, port)
    uvicorn.run(build_app(executor, host, port), host=host, port=port)


if __name__ == "__main__":
    main()
