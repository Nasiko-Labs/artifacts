"""The upload approval: the card (header, options, body with name, folder, size, slide titles) and the
binding the send guard enforces: one tool, one target (folder + file name), one content hash. Pure."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_hitl import question_metadata
from render import size_text
from uploader import UploadPlan

HEADER = "Upload this deck to Drive?"
APPROVE, SKIP = "Approve", "Skip"
FOLDER_HEADER = "Which folder?"
MAX_TITLES_SHOWN = 12
ROOT_FOLDER = "your Drive's top level"


@dataclass(frozen=True)
class Approval:
    plan: UploadPlan
    file_name: str
    folder_id: str | None
    folder_name: str
    sha256: str
    size: int

    @property
    def tool(self) -> str:
        return self.plan.tool

    def to_dict(self) -> dict:
        return {"plan": self.plan.to_dict(), "file_name": self.file_name, "folder_id": self.folder_id,
                "folder_name": self.folder_name, "sha256": self.sha256, "size": self.size}

    @staticmethod
    def from_dict(data: Any) -> Approval | None:
        if not isinstance(data, Mapping) or not data.get("sha256") or not isinstance(data.get("plan"), Mapping):
            return None
        return Approval(plan=UploadPlan.from_dict(data["plan"]), file_name=str(data["file_name"]),
                        folder_id=data.get("folder_id") or None, folder_name=str(data.get("folder_name") or ""),
                        sha256=str(data["sha256"]), size=int(data.get("size") or 0))


def card_text(approval: Approval, titles: list[str]) -> str:
    shown = titles[:MAX_TITLES_SHOWN]
    more = f"\n…and {len(titles) - len(shown)} more" if len(titles) > len(shown) else ""
    listed = "\n".join(f"{n}. {title}" for n, title in enumerate(shown, start=1))
    return (f"**Upload this deck to Drive?**\n\n- File: `{approval.file_name}`\n- Folder: {approval.folder_name}\n"
            f"- Size: {size_text(approval.size)}\n- Slides: {len(titles)}\n\n{listed}{more}\n\n"
            "Approve to upload this exact file once, Skip to keep it here, or type a change "
            "(\"call it Q3 board update\", \"put it in the Board folder\").")


def card_metadata() -> dict:
    return question_metadata(HEADER, [(APPROVE, "Upload this exact file once"), (SKIP, "Don't upload")])


def folder_metadata(folders: list[tuple[str, str]]) -> dict:
    return question_metadata(FOLDER_HEADER, [(label, "") for label in folder_labels(folders)])


def folder_labels(folders: list[tuple[str, str]]) -> list[str]:
    seen: dict[str, int] = {}
    labels = []
    for ident, name in folders:
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name} ({ident[-6:]})")
    return labels


def decision(answer: str) -> str:
    """approve | skip | change (free text)."""
    text = answer.strip().casefold().rstrip(".!")
    if text in ("approve", "approved", "yes", "upload"):
        return "approve"
    if text in ("skip", "no", "cancel", "don't upload"):
        return "skip"
    return "change"


def guard(approval: Approval | None, tool: str, arguments: Mapping[str, Any]) -> str | None:
    """None when this call is exactly the approved upload; else why it is refused (before the gateway)."""
    if approval is None:
        return "no upload was approved"
    plan = approval.plan
    if tool != plan.tool:
        return f"the approved tool is {plan.tool}, not {tool}"
    allowed = {plan.name_key, plan.content_key, plan.mime_key, plan.folder_key} - {None}
    if set(arguments) - allowed:
        return "the call carries arguments that were not approved"
    if arguments.get(plan.name_key) != approval.file_name:
        return "the file name is not the approved one"
    folder = arguments.get(plan.folder_key) if plan.folder_key else None
    folder = folder[0] if isinstance(folder, list) and len(folder) == 1 else folder
    if (folder or None) != approval.folder_id:
        return "the folder is not the approved one"
    try:
        data = base64.b64decode(str(arguments.get(plan.content_key) or ""), validate=True)
    except ValueError:
        return "the content is not the approved file"
    if hashlib.sha256(data).hexdigest() != approval.sha256:
        return "the file bytes are not the approved file"
    return None
