"""Turning a graph turn into a reply or a pause, and answering a paused task (Q1, folder, approval card,
the platform's own approval). One approval = one upload call; the bytes are rebuilt from the stored deck
and must hash to what the card showed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import approval as a
import intake
import prompts
from deps import TurnDeps
from graph import DeckAgent, TurnResult
from sender import GuardedUploader, UploadResult
from session import Built, Stored
from uploader import UploadPlan
from walk import METADATA_KEY, Outcome, card, fallback, offer

AUTHORIZED = "authorized"
CHANGED = ("The deck changed after that card was shown, so nothing was uploaded. Say **upload** to get a card "
           "for the current deck.")
SKIPPED = "Not uploaded. The deck stays in this conversation; say **upload** any time."


async def from_turn(deps: TurnDeps, result: TurnResult) -> Outcome:
    if result.pause:
        p = result.pause
        return Outcome("input_required", p["text"], p["hitl"],
                       {METADATA_KEY: {"stage": p["stage"], "request": p["request"]}})
    if result.upload and result.built is not None:
        return await offer(deps, result.built, lead=result.reply)
    return Outcome("complete", result.reply, built=result.built)


async def resume(deps: TurnDeps, agent: DeckAgent, context_id: str, metadata: Mapping[str, Any],
                 answer: str) -> Outcome:
    data = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(data, Mapping):
        return Outcome("complete", prompts.NOT_WAITING)
    stage = data.get("stage")
    if stage == "content":
        request = intake.content_answer(str(data.get("request") or ""), answer)
        return await from_turn(deps, await agent.turn(request, context_id, deps, accept_content=True))
    if stage == "folder":
        return await _folder(deps, agent, context_id, data, answer)
    if stage == "approve":
        return await _approve(deps, agent, context_id, data, answer)
    if stage == "gateway":
        if answer.strip().casefold() != AUTHORIZED:
            return Outcome("complete", "The upload was denied on the approval card, so nothing was uploaded.")
        return await _upload(deps, agent, context_id, a.Approval.from_dict(data.get("approval")), again=True)
    return Outcome("complete", prompts.NOT_WAITING)


async def _folder(deps, agent, context_id, data, answer) -> Outcome:
    folders = [tuple(f) for f in data.get("folders") or []]
    labels = a.folder_labels(folders)
    chosen = next((f for f, label in zip(folders, labels) if label.casefold() == answer.strip().casefold()), None)
    stored = await agent.stored(context_id)
    if chosen is None or stored is None:
        return await from_turn(deps, await agent.turn(answer, context_id, deps, force_upload=True))
    appr = a.Approval(UploadPlan.from_dict(data["plan"]), str(data["file_name"]), chosen[0], chosen[1],
                      str(data["sha256"]), int(data.get("size") or 0))
    return card(appr, _built(stored, appr.file_name))


async def _approve(deps, agent, context_id, data, answer) -> Outcome:
    decision = a.decision(answer)
    if decision == "skip":
        return Outcome("complete", SKIPPED)
    if decision == "change":  # an edit instruction: rebuild and ask again with the new hash
        return await from_turn(deps, await agent.turn(answer, context_id, deps, force_upload=True))
    return await _upload(deps, agent, context_id, a.Approval.from_dict(data.get("approval")))


def _built(stored: Stored, file_name: str) -> Built:
    data, sha = stored.build()
    return Built(Stored(stored.deck, stored.tables, stored.report, file_name, stored.folder, stored.chart_failures),
                 data, sha)


async def _upload(deps, agent, context_id, appr: a.Approval | None, again: bool = False) -> Outcome:
    stored = await agent.stored(context_id)
    if appr is None or stored is None:
        return Outcome("complete", "Nothing was uploaded: the approval could not be read. Say **upload** to retry.")
    built = _built(stored, appr.file_name)
    if built.sha != appr.sha256:
        return Outcome("complete", CHANGED, built=built)
    result = await GuardedUploader(deps, appr).upload(built.data)
    return _after(result, appr, built, again)


def _after(result: UploadResult, appr: a.Approval, built: Built, again: bool) -> Outcome:
    if result.status == "uploaded":
        link = f"\n\nOpen it: {result.links[0]}" if result.links else ""
        return Outcome("complete", f"Uploaded `{appr.file_name}` to {appr.folder_name}.{link}", built=built,
                       uploaded={"file_name": appr.file_name, "folder": appr.folder_name, "sha256": appr.sha256,
                                 "link": result.links[0] if result.links else None})
    if result.status == "paused" and not again and result.hitl_request_id:
        return Outcome("auth_required", "The platform needs your approval before this upload runs.",
                       {"hitl_request_id": result.hitl_request_id},
                       {METADATA_KEY: {"stage": "gateway", "approval": appr.to_dict()}}, built)
    if result.status == "blocked":
        return fallback(built, "Uploading is blocked for this agent, so nothing was uploaded.")
    if result.status == "denied":
        return Outcome("complete", "The upload was denied, so nothing was uploaded and I won't retry it.", built=built)
    reason = result.reason or "the approval could not be used"
    return fallback(built, f"Not uploaded: {reason}. I won't retry it automatically (a retry could create a "
                           "duplicate file).")
