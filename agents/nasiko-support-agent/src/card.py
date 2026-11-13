"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: routing sees only the name and description.
It ends with the knowledge snapshot's date, taken from src/kb/facts.json; scripts/compile_kb.py writes the
same text into AgentCard.json, so the two never drift."""

from kb import load

AGENT_NAME = "nasiko-support-agent"
AGENT_VERSION = "1.0.1"
_DESCRIPTION_BODY = (
    "Answers questions about using Nasiko itself. How-to: where a setting lives and how to do a task, such as "
    "deploying an agent from a zip, GitHub or a registry, what an agent zip must contain, versions, logs, "
    "secrets, chat and the orchestrator, workflows, the LLM router, MCP servers and tool permissions, approvals, "
    "sessions, flows and traces, TokenOps costs and budgets, coding harnesses, access control and the CLI. "
    "It replies with the exact menu path, numbered steps, the matching `nasiko` CLI command and a link to "
    "docs.nasiko.com. Troubleshooting: paste an error, a build log or `nasiko logs` output and it matches known "
    "failures (zip layout, corrupt zip, version already exists, 502 from chat, expired CLI login, rejected "
    "provider key, missing dependency, wrong port, GitHub or MCP server not connected, stuck pending) to a likely cause and fix, asking at most "
    "two short questions first. It answers only from its bundled Nasiko notes, never invents a menu or command, "
    "and says so when something isn't in its notes. It only explains: it can't see your agents or change "
    "anything. Not for general coding or other topics."
)


def description(snapshot_date: str) -> str:
    return f"{_DESCRIPTION_BODY} Notes as of {snapshot_date}." if snapshot_date else _DESCRIPTION_BODY


AGENT_DESCRIPTION = description(load().facts.snapshot_date)

SKILL_ID = "nasiko-support"
SKILL_NAME = "Nasiko how-to and troubleshooting"
SKILL_DESCRIPTION = (
    "Where to click and which `nasiko` command to run for a Nasiko task, and the likely cause and fix for an "
    "agent that won't build, start or answer, from the error text you paste. Answers cite docs.nasiko.com."
)
SKILL_TAGS = ["nasiko", "support", "how-to", "troubleshooting", "deploy", "llm router", "mcp", "cli", "docs"]
SKILL_EXAMPLES = [
    "How do I upload an agent, and what should the zip contain?",
    "My agent isn't working, what do I do?",
    "How do I configure the LLM router?",
]
