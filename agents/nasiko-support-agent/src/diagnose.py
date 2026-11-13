"""Pure: the troubleshooting playbook. Known error signatures are matched in code against what the user
pastes, and each maps to a fixed cause and fix. No model is involved, so the fix is always the same for
the same evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signature:
    id: str
    pattern: re.Pattern
    cause: str
    fix: tuple[str, ...]
    cli: tuple[str, ...]
    flow_id: str                     # the KB flow whose docs link is cited
    also: re.Pattern | None = None   # a second pattern that must also match
    unless: re.Pattern | None = None  # wording that means a different cause: no match when present

    def matches(self, text: str) -> bool:
        return (bool(self.pattern.search(text)) and (self.also is None or bool(self.also.search(text)))
                and (self.unless is None or not self.unless.search(text)))


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


SIGNATURES: tuple[Signature, ...] = (
    Signature(
        "zip-layout",
        _rx(r"no dockerfile (?:at|in) the (?:root|zip)|no dockerfile in the zip|dockerfile has no from line"
            r"|no from line"),
        "The zip's layout: the Dockerfile isn't at the root of the zip (usually the folder was zipped instead "
        "of its contents), or it has no FROM line.",
        ("Zip the folder's contents, not the folder: inside the folder, select everything and zip that.",
         "Make sure the Dockerfile at the root starts with a FROM line.",
         "Check it before uploading with `nasiko validate`, then upload again."),
        ("nasiko validate", "nasiko upload ./my-agent.zip -n my-agent"),
        "build-failed",
    ),
    Signature(
        "corrupt-zip",
        _rx(r"invalid zip|\bEOCD\b|end of central directory|not a (?:valid )?zip|zip (?:file )?is corrupt"
            r"|corrupt(?:ed)? zip|unexpected end of (?:zip|archive)"),
        "The zip is corrupt or only partly uploaded.",
        ("Create the zip again from the folder's contents.",
         "Upload it again (on **Fleet → Agents → Builds** use **Deploy again**, or from the CLI)."),
        ("nasiko upload ./my-agent.zip -n my-agent",),
        "build-failed",
    ),
    Signature(
        "version-exists",
        _rx(r"\bversion\s+\S*\s*already exists|\balready exists\b.{0,40}\bversion\b"
            r"|\b409\b.{0,80}\bversion\b|\bversion\b.{0,80}\b409\b"),
        "The same version was uploaded again. Versions can't be overwritten.",
        ("Bump `version` in AgentCard.json (or pyproject.toml), for example from 1.0.0 to 1.0.1.",
         "Or set the new version when you upload: on the Deploy page, or with `-v` in the CLI."),
        ("nasiko upload . -v 1.0.1",),
        "new-version-reupload",
    ),
    Signature(
        "chat-502",
        _rx(r"\b502\b|bad gateway|no running agent"),
        "The agent isn't running yet, or it crashed on start.",
        ("If you just deployed it, wait about 15 seconds and try again.",
         "Check its status in **Fleet → Agents**, or run `nasiko ps`.",
         ("Read the logs (`nasiko logs my-agent -n 100`): the server must listen on port 8000 "
          "(or the port you set).")),
        ("nasiko ps", "nasiko logs my-agent -n 100"),
        "agent-wont-start",
    ),
    Signature(
        "cli-login-expired",
        _rx(r"invalid token|token (?:has )?expired|session (?:has )?expired|nasiko auth login"
            r"|(?:\b401\b|unauthori[sz]ed).{0,80}\b(?:nasiko|cli|log ?in again|please log in)\b"),
        "Your CLI login has expired (or the token is no longer valid).",
        ("Sign in again with `nasiko auth login`, then repeat the command.",),
        ("nasiko auth login",),
        "install-connect-login",
        unless=_rx(r"api[ _-]?key|\b(?:openai|anthropic|provider|gateway|mcp)\b|tools/call"),
    ),
    Signature(
        "provider-key-rejected",
        _rx(r"\b401\b|unauthori[sz]ed|authentication ?error|incorrect api key|invalid (?:x-)?api[ _-]?key"),
        "The model provider rejected the API key: the key your agent or its LLM router config uses is wrong, "
        "expired or for a different provider.",
        (("Open **Connect → LLM router**, edit the config the agent uses and check its **Key**: choose "
          "**Add a key** and paste a working key for that provider."),
         "If the agent reads the key itself, set it again as a secret (and rotate it if it was ever pasted in chat)."),
        ("nasiko secrets set OPENAI_API_KEY <value>", "nasiko llm-config get my-agent"),
        "provider-keys",
        also=_rx(r"api[ _-]?key|\bkey\b|\b(?:openai|anthropic|provider|llm|model|router|gpt|claude)\b"),
        unless=_rx(r"\bmcp\b|\bgateway\b|tools/call|nasiko auth"),
    ),
    Signature(
        "missing-dependency",
        _rx(r"ModuleNotFoundError|No module named|Cannot find module|ImportError"),
        "A dependency is missing from the image.",
        ("Add the missing package to requirements.txt (or your package.json / pyproject.toml).",
         "Upload it as a new version: the same version is rejected."),
        ("nasiko upload . -v 1.0.1",),
        "agent-wont-start",
    ),
    Signature(
        "wrong-port",
        _rx(r"address already in use|EADDRINUSE|not listening on (?:port|0\.0\.0\.0|\$PORT|:?\d{2,5}\b)"
            r"|\bport \d{2,5} (?:is )?not open|(?:readiness|startup) probe failed"),
        "The agent isn't listening where Nasiko expects it.",
        (("Make the server listen on 0.0.0.0 and on `$PORT` (default 8000), the same port the agent was "
          "deployed with."),
         "Upload a new version and check the logs."),
        ("nasiko logs my-agent -n 100",),
        "agent-wont-start",
    ),
    Signature(
        "llm-403",
        _rx(r"\b403\b|forbidden"),
        "The model call was refused part-way through the turn: the turn's model-access window or its limits "
        "were exceeded.",
        ("Retry the message.",
         "If it keeps happening, tell your workspace admin: it's a platform setting, not your agent's code."),
        (),
        "configure-llm-router",
        also=_rx(r"\b(?:llm|model|openai|anthropic|completions?|router|gpt|claude)\b"),
    ),
    Signature(
        "github-not-connected",
        _rx(r"github (?:is |isn'?t |is not )?(?:not )?connected|github connection (?:has )?expired"
            r"|reconnect github"),
        "Nasiko isn't connected to your GitHub account (or the connection expired), so it can't clone the repo.",
        ("Open **Fleet → Agents**, click **Deploy an agent** and choose **From GitHub**.",
         "Click **Connect GitHub** (or **Reconnect GitHub**) and finish in the GitHub window, then deploy again."),
        ("nasiko github connect", "nasiko github status"),
        "deploy-agent-github",
        also=_rx(r"not connected|isn'?t connected|expired|reconnect"),
    ),
    Signature(
        "mcp-not-connected",
        _rx(r"source (?:isn'?t|is not|not) connected"
            r"|\b(?:mcp|connector|tools?)\b.{0,40}\b(?:isn'?t|is not|not) connected"
            r"|\b(?:isn'?t|is not|not) connected\b.{0,40}\b(?:mcp|connector)\b"
            r"|\bno tools (?:available|found)\b|\btool .{0,40}not (?:enabled|available)"),
        "The MCP server isn't connected for you, or it isn't enabled for this agent.",
        ("Open **Connect → MCP servers** and connect the server with your own credential.",
         "Open the agent from **Fleet → Agents**, go to its **MCP** tab and switch the server on."),
        ("nasiko mcp connections", "nasiko mcp agent-tools enable my-agent <connector-id>"),
        "mcp-connect-and-enable",
        unless=_rx(r"\bgithub\b"),
    ),
    Signature(
        "pending-no-logs",
        _rx(r"\bpending\b"),
        "The agent is waiting for room on the cluster (no capacity yet), so it has not started and has no logs.",
        ("Wait a few minutes and check `nasiko ps` again.",
         "If it stays pending, contact your workspace admin: the cluster needs more capacity."),
        ("nasiko ps",),
        "agent-wont-start",
        also=_rx(r"no logs|stuck|forever|for (?:\d+ )?(?:minutes|hours|ages)|still pending|won'?t start"),
    ),
)


def match(text: str) -> list[Signature]:
    """Every signature found in `text`, most specific first."""
    return [sig for sig in SIGNATURES if sig.matches(text or "")]
