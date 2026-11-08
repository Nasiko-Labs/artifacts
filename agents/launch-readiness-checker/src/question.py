"""Pure: the one question this agent may pause on, "Which launch should I check?", answered with free text.
The platform's structured question needs at least one option, so the card offers Cancel plus free text. The
pause carries the parsed request, so it resumes on any replica from the task's own metadata."""

from __future__ import annotations

from dataclasses import dataclass

import replies
from nasiko_hitl import question_metadata

METADATA_KEY = "launch_readiness_pause"


@dataclass(frozen=True)
class Pause:
    text: str
    hitl: dict
    metadata: dict


def launch_question(request: dict) -> Pause:
    hitl = question_metadata(replies.ASK_LAUNCH_NAME, [(replies.CANCEL, "Stop here")], allow_custom_input=True)
    return Pause(replies.ASK_LAUNCH_TEXT, hitl, {METADATA_KEY: {"kind": "launch", "request": request}})


def is_cancel(answer: str) -> bool:
    return (answer or "").strip().casefold() in {"cancel", "skip", "stop", "no", "never mind"}
