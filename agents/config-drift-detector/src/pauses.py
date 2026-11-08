"""The one question this agent may pause on ("Which IaC file defines this?", free text, rendered by the
chat's request card) and what the resume needs: the original message, kept on the task's metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from nasiko_hitl import question_metadata

METADATA_KEY = "config_drift_detector"
KIND = "iac_path"
_OPTION = ("I'll paste the file", "Paste it after a line === path/to/file === in your reply.")


@dataclass(frozen=True)
class Pending:
    query: str
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": KIND, "query": self.query, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != KIND or not data.get("query"):
            return None
        return Pending(query=str(data["query"]), wants_json=bool(data.get("wants_json")))


def question() -> tuple[str, dict]:
    """(chat text, status-message metadata): free text is the expected answer."""
    return prompts.ASK_PATH, question_metadata(prompts.ASK_PATH_HEADER, [_OPTION], allow_custom_input=True)
