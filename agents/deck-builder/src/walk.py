"""The upload offer and its pauses, kept on the A2A task between turns (never in the graph):

    offer → discover → map the upload tool ─┬─ none / unmappable → fallback script (no card)
                                            ├─ several folders match → [Which folder?] → card
                                            └─ card [Approve / Skip / typed change]
    Approve → rebuild from the stored deck, hash must equal the approved one → ONE guarded call
            → gateway ask → [auth-required] → "authorized" re-calls the identical upload once
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import approval as a
import discovery
import render
import script_export
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from session import Built
from tool_health import ToolHealth
from uploader import parse_folders, pick_folder_lookup, pick_upload

log = logging.getLogger("deck_builder.walk")

METADATA_KEY = "deck_builder"
CANT_UPLOAD = "This drive connection can't take a file upload from here."


@dataclass
class Outcome:
    kind: str                       # complete | input_required | auth_required
    text: str
    hitl: dict | None = None
    metadata: dict | None = None
    built: Built | None = None
    uploaded: dict | None = None
    upload_unavailable: bool = False


def pause(stage: str, text: str, hitl: dict, built: Built | None = None, **data) -> Outcome:
    return Outcome("input_required", text, hitl, {METADATA_KEY: {"stage": stage, **data}}, built)


def fallback(built: Built, reason: str, lead: str = "") -> Outcome:
    script = script_export.script(built.stored.deck, built.stored.tables, built.stored.file_name)
    text = "\n\n".join(p for p in (lead, reason, render.script_block(script)) if p)
    return Outcome("complete", text, built=built, upload_unavailable=True)


async def offer(deps: TurnDeps, built: Built, lead: str = "") -> Outcome:
    tools, note = await discovery.discover(deps)
    if note:
        return fallback(built, note, lead)
    plan, seen = pick_upload(tools)
    if plan is None:
        return fallback(built, CANT_UPLOAD if seen else discovery.NO_GATEWAY, lead)
    folder_id, folder_name, folders, note = None, a.ROOT_FOLDER, [], ""
    if built.stored.folder:
        folders, note = await _find_folder(deps, tools, built.stored.folder)
        if len(folders) == 1:
            folder_id, folder_name = folders[0][0], folders[0][1]
        elif len(folders) > 1:
            text = "\n\n".join(p for p in (lead, f"Several folders match \"{built.stored.folder}\". Which one?") if p)
            return pause("folder", text, a.folder_metadata(folders), built, plan=plan.to_dict(), folders=folders,
                         file_name=built.stored.file_name, sha256=built.sha, size=len(built.data))
    appr = a.Approval(plan, built.stored.file_name, folder_id, folder_name, built.sha, len(built.data))
    return card(appr, built, "\n\n".join(p for p in (lead, note) if p))


def card(appr: a.Approval, built: Built, lead: str = "") -> Outcome:
    titles = [s.title for s in built.stored.deck.slides]
    text = "\n\n".join(p for p in (lead, a.card_text(appr, titles)) if p)
    return pause("approve", text, a.card_metadata(), built, approval=appr.to_dict())


async def _find_folder(deps: TurnDeps, tools: tuple[McpTool, ...], name: str) -> tuple[list, str]:
    lookup = pick_folder_lookup(tools)
    missing = f"I couldn't find a folder named \"{name}\", so it will go to {a.ROOT_FOLDER}."
    if lookup is None:
        return [], missing
    tool, key = lookup
    try:
        result = await ToolHealth(deps.gateway).call_tool(tool.name, {key: name}, deps.traceparent)
    except McpGatewayError:
        return [], missing
    if not isinstance(result, ToolOk) or result.is_error:
        return [], missing
    matches = [f for f in parse_folders(result.text) if name.casefold() in f[1].casefold()]
    return matches, "" if matches else missing
