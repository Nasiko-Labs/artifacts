"""One turn's work. A paste (or envelope, or file part) is analysed in code and explained by one model call;
a message with no paste is a follow-up on the stored report, a request to read the cluster through an
enabled read-only connector, or a scope check. Change requests are refused in code, never acted on."""

from __future__ import annotations

import re

import analysis
import connector
import discovery
import explain
import followup
import intake
import prompts
import smalltalk
from inputs import Incoming
from llm import TurnDeps
from outcome import TurnResult, finish, reply

_CLUSTER_WORDS = re.compile(r"\b(?:cluster|kubernetes|k8s|kubectl|pods?|nodes?|deploy(?:ment)?s?|namespaces?|"
                            r"crash ?loop\w*|oom\w*|health|rollouts?|replica ?sets?|events?)\b", re.IGNORECASE)
_LABEL = re.compile(r"\bcluster\s+([a-z0-9][-a-z0-9_.]{1,62})\b", re.IGNORECASE)
_NOT_LABELS = frozenset({"health", "is", "status", "state", "looks", "overview", "and", "the", "my", "please",
                         "now", "today", "pods", "nodes", "events"})
_LOOKS_PASTED = 200


def cluster_label(instruction: str, previous: str = "") -> str:
    match = _LABEL.search(instruction or "")
    if match and match.group(1).casefold() not in _NOT_LABELS:
        return match.group(1)
    return previous


async def turn(incoming: Incoming, stored: dict | None, deps: TurnDeps) -> TurnResult:
    if not incoming.file_text:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            return reply(canned, incoming.notes)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    split = intake.split(prompts.strip_markers(incoming.paste))
    if split.blocks:
        return await analyse(incoming, deps, split, stored)
    if stored and stored.get("result"):
        return await followup.answer(incoming, deps, stored, split.instruction)
    return await no_paste(incoming, deps, split)


async def analyse(incoming: Incoming, deps: TurnDeps, split: intake.Split, stored: dict | None,
                  extra_notes: tuple[str, ...] = ()) -> TurnResult:
    blocks = merge((stored or {}).get("blocks") or [], split.blocks)
    label = cluster_label(split.instruction, (stored or {}).get("label", ""))
    result = analysis.run(blocks, split.unparsed, label)
    cache = (stored or {}).get("explained") or {}
    explained = await explain.explain(deps, result["issues"], split.instruction, incoming.context, cache)
    if explained.status == prompts.OFF_TOPIC and split.instruction and not _CLUSTER_WORDS.search(split.instruction):
        return reply(prompts.DECLINE)
    result = {**result, "issues": explained.issues}
    notes = [*extra_notes, *incoming.notes]
    if explained.status == "template":
        notes.append("The explanations are fixed templates: the model could not be reached for this turn.")
    if explained.replaced:
        notes.append(f"{explained.replaced} model explanation(s) named something not in your paste and were "
                     "replaced by a template line.")
    lead = prompts.READ_ONLY if followup.wants_change(split.instruction) else ""
    new_stored = {"blocks": [_store(b) for b in blocks], "label": label, "result": result,
                  "explained": explain.cache_of(result["issues"])}
    return finish(result, incoming, tuple(notes), lead, new_stored)


async def no_paste(incoming: Incoming, deps: TurnDeps, split: intake.Split) -> TurnResult:
    text = split.instruction
    if split.unparsed or (len(incoming.paste) >= _LOOKS_PASTED and "\n" in incoming.paste.strip()):
        return reply(prompts.paste_list(prompts.NOTHING_RECOGNISED), incoming.notes)
    if followup.wants_change(text):
        return reply(prompts.READ_ONLY + "\n\n" + prompts.paste_list(prompts.PASTE_LEAD))
    if followup.wants_next(text):
        return reply(prompts.paste_list(prompts.PASTE_LEAD))
    if _CLUSTER_WORDS.search(text):
        return await from_connector(incoming, deps, split)
    status = await followup.classify(deps, incoming, has_result=False)
    if status == "off_topic":
        return reply(prompts.DECLINE)
    return reply(prompts.paste_list(prompts.PASTE_LEAD))


async def from_connector(incoming: Incoming, deps: TurnDeps, split: intake.Split) -> TurnResult:
    if deps.gateway is None:
        return reply(prompts.paste_list(prompts.NOT_CONNECTED))
    catalog = await discovery.discover(deps)
    if catalog is None or not catalog.read_tools():
        return reply(prompts.paste_list(prompts.NOT_CONNECTED))
    fetched = await connector.fetch(deps, catalog)
    if not fetched.called:
        return reply(prompts.paste_list(prompts.NOT_CONNECTED))
    fresh = intake.split(prompts.strip_markers(fetched.text))
    notes = tuple(f"Connector: {n}." for n in fetched.notes)
    if not fresh.blocks:
        lead = ("The cluster connector returned nothing I can read as kubectl output, so I won't guess. Run these "
                "read-only commands and paste their output:")
        return reply(prompts.paste_list(lead), notes)
    return await analyse(incoming, deps, intake.Split(fresh.blocks, fresh.unparsed, split.instruction), None, notes)


def merge(previous: list[dict], new: tuple[intake.Block, ...]) -> list[intake.Block]:
    """Earlier blocks plus this turn's; a block of the same type and command (or the same described object)
    is replaced by the newer one."""
    restored = [_restore(b) for b in previous]
    keyed = {_key(b): b for b in restored}
    for block in new:
        keyed.pop(_key(block), None)
        keyed[_key(block)] = block
    return list(keyed.values())


def _key(block: intake.Block) -> tuple:
    if block.type in intake.DESCRIBE_TYPES:
        name = next((ln.split(":", 1)[1].strip() for ln in block.lines if ln.startswith("Name:")), "")
        namespace = next((ln.split(":", 1)[1].strip() for ln in block.lines if ln.startswith("Namespace:")), "")
        return block.type, namespace, name
    return block.type, block.label


def _store(block: intake.Block) -> dict:
    return {"type": block.type, "label": block.label, "header": block.header, "lines": list(block.lines),
            "describe": block.describe}


def _restore(data: dict) -> intake.Block:
    return intake.Block(type=data.get("type"), label=data.get("label") or "", header=data.get("header") or "",
                        lines=list(data.get("lines") or []), describe=bool(data.get("describe")))
