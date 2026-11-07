import logging
import os

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

# Telemetry must initialize before a2a-sdk (and Starlette) is imported: the Starlette
# instrumentor rebinds the class, and an earlier import keeps the un-instrumented one.
from telemetry import TraceparentMiddleware, init_telemetry  # noqa: E402

init_telemetry(os.environ.get("OTEL_SERVICE_NAME", "contract-review"))

import click  # noqa: E402
import uvicorn  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from agent_executor import ContractReviewExecutor  # noqa: E402
from nasiko_state import install_shutdown_hooks, make_task_store  # noqa: E402

logger = logging.getLogger(__name__)

DESCRIPTION = 'Reviews an MSA against the company playbook: one pass or break per playbook rule, quoting the contract clause and the rule. Pauses for the human decision on the breaks, then records a decision log in the workspace after approval. It does not search the web and does not email.'
SKILL_DESCRIPTION = 'Checks a master subscription agreement against the company playbook and reports pass or break for every rule, quoting the clause and the rule. Stops to ask how to handle the breaks (accept, request a redline, or escalate), then writes the decision log to the workspace once the save is approved. Does not search the web and does not send email.'


def build_card(url: str) -> AgentCard:
    return AgentCard(
        name="Contract Review",
        description=DESCRIPTION,
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url)],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(
            id="msa_review",
            name="MSA review",
            description=SKILL_DESCRIPTION,
            tags=['contract review', 'MSA', 'playbook', 'legal', 'redline', 'decision log'],
            examples=[
                "Review the Northwind MSA against our playbook and stop to ask me how to handle the breaks.",
                "Review the MSA and stop on each break separately.",
                "Request a redline of the cap and the term, escalate residency, accept the breach timing "
                "for this deal only.",
            ],
        )],
    )


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=int(os.environ.get("PORT", "8000")), type=int)
def main(host: str, port: int):
    card = build_card(os.getenv("HOST_OVERRIDE", f"http://{host}:{port}/"))
    handler = DefaultRequestHandler(agent_executor=ContractReviewExecutor(), task_store=make_task_store(),
                                    agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/"))
    install_shutdown_hooks(app)
    app = TraceparentMiddleware(app)
    logger.info("Contract Review agent listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
