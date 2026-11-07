"""The decision log and the tool guard.

The log is rendered in code from the validated findings and the parsed decisions, and the
`save_file` call is built in code, not by the model. `GuardedGateway` is the in-code check the
generic workspace tool cannot do: a save must target exactly `NORTHWIND_DECISION_LOG.md` and
carry exactly the log shown to the human. Which tools the agent has is the platform's grant.
A refused call never reaches the network. (There is no email tool; nothing here can send.)
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from nasiko_mcp import ToolDenied, ToolResult
from review import ACTION_LABELS, Finding

READ_TOOL = "read_file"
SAVE_TOOL = "save_file"
DECISION_LOG_PATH = "NORTHWIND_DECISION_LOG.md"
DEFAULT_MSA_PATH = "NORTHWIND_MSA.md"
MSA_PATH_ENV = "CONTRACT_MSA_PATH"
DISCLAIMER = "Drafting aid, not legal advice. The reviewing lawyer approved these decisions."


def msa_path(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return (source.get(MSA_PATH_ENV) or "").strip() or DEFAULT_MSA_PATH


def render_log(findings: Sequence[Finding], decisions: Sequence[Mapping[str, Any]], today: str) -> str:
    by_rule = {d["rule_index"]: d for d in decisions}
    lines = ["# Northwind MSA - decision log", "", DISCLAIMER, f"Recorded: {today}", "", "## Breaks"]
    for f in findings:
        if not f.is_break:
            continue
        decision = by_rule[f.rule_index]
        lines += ["", f"### Rule {f.rule_index}: {f.clause_heading}",
                  f"- Decision: {ACTION_LABELS[decision['action']]}"
                  + (f" ({decision['note']})" if decision.get("note") else ""),
                  f"- Recommended: {ACTION_LABELS[f.recommended_action]}",
                  f'- Clause: "{f.clause_quote}"', f'- Rule: "{f.rule_quote}"', f"- Why: {f.reason}"]
    lines += ["", "## Passed (no action)"]
    lines += [f'- Rule {f.rule_index}: {f.clause_heading} - "{f.clause_quote}"' for f in findings if not f.is_break]
    return "\n".join(lines) + "\n"


def save_arguments(content: str) -> dict:
    return {"path": DECISION_LOG_PATH, "content": content}


def check_call(name: str, arguments: Mapping[str, Any], approved_content: str | None) -> str | None:
    """Why this call is refused, or None when it may go to the gateway."""
    if name != SAVE_TOOL:
        return None
    if arguments.get("path") != DECISION_LOG_PATH:
        return f"save_file may only write {DECISION_LOG_PATH}"
    if set(arguments) - {"path", "content", "encoding"} or arguments.get("encoding", "utf8") != "utf8":
        return "save_file accepts only path and utf8 content"
    if approved_content is None or arguments.get("content") != approved_content:
        return "save_file content must be exactly the decision log shown to the user"
    return None


class GuardedGateway:
    """A ToolCaller that enforces `check_call` and remembers the last outcome per tool."""

    def __init__(self, inner: Any, approved_content: str | None = None):
        self._inner = inner
        self._approved = approved_content
        self.outcomes: dict[str, ToolResult] = {}

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        refusal = check_call(name, arguments, self._approved)
        if refusal is not None:
            outcome: ToolResult = ToolDenied(name, f"refused by the agent: {refusal}", by_user=False)
        elif self._inner is None:
            outcome = ToolDenied(name, "the workspace tools are not connected for this agent", by_user=False)
        else:
            outcome = await self._inner.call_tool(name, arguments, traceparent)
        self.outcomes = {**self.outcomes, name: outcome}
        return outcome
