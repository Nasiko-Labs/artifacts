"""The four lenses as four independent, concurrent model calls, plus the one refill call.

Anti-anchoring: each voice gets only the numbered plan, the context and the org policy: never another
voice's output and never the conversation. Ids are assigned in code ("v2.3" = Pragmatist, third story)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from llm import LlmError, TurnDeps, complete_json
from specific import Candidate, from_model

log = logging.getLogger("pre-mortem.voices")

LENSES = (("v1", "skeptic"), ("v2", "pragmatist"), ("v3", "critic"), ("v4", "architect"))
LENS_IDS = {lens: vid for vid, lens in LENSES}
MAX_STORIES_PER_VOICE = 4
OK, OFF_TOPIC, FAILED = "ok", "off_topic", "failed"


@dataclass(frozen=True)
class VoiceResult:
    lens: str
    status: str
    candidates: tuple[Candidate, ...] = ()


def validate_voice(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC, "failure_modes": []}
    modes = data.get("failure_modes")
    if data.get("status") not in (OK, None) or not isinstance(modes, list):
        return None
    return {"status": OK, "failure_modes": modes}


async def run_voices(deps: TurnDeps, numbered: str, context: Sequence[str],
                     policy: Sequence[str] = ()) -> list[VoiceResult]:
    jobs = [_voice(deps, lens, vid, numbered, context, policy) for vid, lens in LENSES]
    return list(await asyncio.gather(*jobs))


async def refill(deps: TurnDeps, lens: str, numbered: str, context: Sequence[str], used_lines: Sequence[int],
                 start: int, policy: Sequence[str] = ()) -> VoiceResult:
    """One more call to `lens`, told which lines are already used; ids continue after `start`."""
    taken = ", ".join(f"L{n}" for n in sorted(set(used_lines))) or "none"
    return await _voice(deps, lens, LENS_IDS[lens], numbered, context, policy, avoid=taken, start=start)


async def _voice(deps: TurnDeps, lens: str, vid: str, numbered: str, context: Sequence[str],
                 policy: Sequence[str], avoid: str = "", start: int = 0) -> VoiceResult:
    messages = prompts.voice_messages(lens, numbered, context, policy, avoid=avoid)
    try:
        data = await complete_json(deps, messages, prompts.VOICE_CONTRACT, validate_voice)
    except LlmError as e:
        log.warning("voice %s failed: %s", lens, e)
        return VoiceResult(lens, FAILED)
    if data["status"] == OFF_TOPIC:
        return VoiceResult(lens, OFF_TOPIC)
    raws = data["failure_modes"][:MAX_STORIES_PER_VOICE]
    built = (from_model(raw, f"{vid}.{start + i}", lens) for i, raw in enumerate(raws, start=1))
    return VoiceResult(lens, OK, tuple(c for c in built if c is not None))
