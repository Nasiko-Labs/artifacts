"""
Comms Follow-up Assistant — Nasiko Agent
Entry point: A2A JSON-RPC server for an agent that pulls context from Slack and
Gmail (and, optionally, the web) through the Nasiko MCP gateway and drafts
follow-up-aware emails and Slack replies (see conversation_loop.py).
"""

import json
import logging
import os
import sys
from pathlib import Path

from telemetry import TraceparentMiddleware, init_telemetry


def _configure_logging() -> None:
    """Tool-call/stage lines from conversation_loop on stdout (`nasiko logs`).
    Its own handler, because opentelemetry-instrument may already have
    configured the root logger, which makes basicConfig a no-op."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("comms_followup")
    logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    logger.propagate = False


_configure_logging()

init_telemetry("comms-followup-agent")

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_executor import CommsFollowupExecutor
from nasiko_state import install_shutdown_hooks, make_task_store

_CARD_PATH = Path(__file__).parent.parent / "AgentCard.json"


def load_agent_card(url: str) -> AgentCard:
    """Build the SDK card from AgentCard.json — the file stays the single
    source of card data (it's what `nasiko validate` and publish read); only
    the serving URL is runtime information."""
    card = json.loads(_CARD_PATH.read_text())
    capabilities = card.get("capabilities", {})
    return AgentCard(
        name=card["name"],
        description=card["description"],
        version=card["version"],
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url)],
        default_input_modes=card.get("defaultInputModes", ["text/plain"]),
        default_output_modes=card.get("defaultOutputModes", ["text/plain"]),
        capabilities=AgentCapabilities(
            streaming=capabilities.get("streaming", True),
            push_notifications=capabilities.get("pushNotifications", False),
        ),
        skills=[
            AgentSkill(
                id=skill["id"],
                name=skill["name"],
                description=skill["description"],
                tags=skill.get("tags", []),
                examples=skill.get("examples", []),
            )
            for skill in card.get("skills", [])
        ],
    )


async def health(_request):
    return JSONResponse({"status": "ok", "agent": "comms-followup-agent"})


def main() -> None:
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))
    agent_card = load_agent_card(os.getenv("HOST_OVERRIDE", f"http://{host}:{port}/"))

    executor = CommsFollowupExecutor()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        # Durable when the platform injects NASIKO_STATE_URL (a task paused for
        # approval survives a pod restart); in-memory otherwise.
        task_store=make_task_store(),
        agent_card=agent_card,
    )
    routes = [
        Route("/health", health),
    ]
    routes += create_agent_card_routes(agent_card)
    routes += create_jsonrpc_routes(handler, rpc_url="/")
    app = Starlette(routes=routes)
    install_shutdown_hooks(app)  # flush pending task writes on SIGTERM; before wrapping
    app = TraceparentMiddleware(app)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
