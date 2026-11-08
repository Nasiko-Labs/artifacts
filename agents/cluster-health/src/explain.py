"""The explain step: one batched model call for the top issues, checked in code.

Every name or number the model writes (a token with a digit, `-`, `/` or `.` inside) must appear in that
issue's own evidence or fields, an explanation is at most 40 words, a likely cause at most 15, and nothing may
contain a kubectl command; anything that fails is replaced by the kind's template line (or dropped, for a
cause). `check_next` is only a template id; code maps it to an allowed, filled command."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import commands
import llm
import prompts
from llm import TurnDeps

MAX_EXPLAINED = 8
MAX_EXPLANATION_WORDS = 40
MAX_CAUSE_WORDS = 15
MAX_CAUSES = 3
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]*[A-Za-z0-9]|\d")
_PLAIN_HYPHENATED = frozenset({
    "out-of-memory", "read-only", "non-zero", "crash-loop", "back-off", "liveness-probe", "readiness-probe",
    "health-check", "up-to-date", "rate-limit", "re-run", "in-progress", "long-running", "built-in",
    "co-located", "on-call", "node-level", "pod-level", "start-up", "self-healing", "multi-container",
})
_COMMAND = re.compile(r"\bkubectl\b|\b(?:delete|drain|cordon|scale|exec|patch|apply)\s+(?:pod|node|deploy)",
                      re.IGNORECASE)

TEMPLATES: Mapping[str, str] = {
    "crash_loop": "CrashLoopBackOff: the container keeps exiting; check the previous container's logs.",
    "oom_killed": "The container was OOMKilled: it used more memory than its limit; check limits and usage.",
    "image_pull": "The image cannot be pulled: check the image name, tag and registry credentials.",
    "pending_unschedulable": "The pod is not scheduled: no node fits its requests, taints or volumes.",
    "restart_storm": "The container restarts often; check the previous logs for the exit reason.",
    "not_ready": "The pod runs but a container fails its readiness check.",
    "evicted_or_failed": "The pod was evicted or failed; describe it for the reason.",
    "node_not_ready": "The node is not ready or is cordoned; describe it for its conditions.",
    "node_pressure": "The node reports a pressure condition and may evict pods.",
    "resource_pressure": "Usage is near capacity; pods may be throttled or evicted.",
    "rollout_unhealthy": "The workload has fewer ready replicas than desired.",
    "recent_rollout": "A new replica set was rolled out recently.",
    "warning_events": "Repeated warning events for this object.",
}
CONTRACT = ('{"status": "ok", "explanations": [{"id": "i1", "explanation": "...", "likely_causes": ["..."], '
            '"check_next": "<template id>"}]} or {"status": "off_topic"}')


@dataclass(frozen=True)
class Explained:
    issues: list[dict]
    status: str            # ok | off_topic | template (no usable model answer)
    replaced: int = 0      # explanations replaced by the template line


def templated(issues: Sequence[dict]) -> list[dict]:
    return [{**i, "explanation": i.get("explanation") or TEMPLATES.get(i["kind"], "")} for i in issues]


def validate(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", prompts.OFF_TOPIC):
        return None
    if data["status"] == "ok" and not isinstance(data.get("explanations"), list):
        return None
    return dict(data)


async def explain(deps: TurnDeps, issues: Sequence[dict], instruction: str, context: Sequence[str],
                  cache: Mapping[str, dict] | None = None) -> Explained:
    """Explanations for the top issues; a cached one (same kind, object and evidence) is reused."""
    cache = cache or {}
    reused = [{**i, **cache[key(i)]} if key(i) in cache else i for i in issues]
    todo = [i for i in reused[:MAX_EXPLAINED] if not i.get("explanation")]
    if not todo:
        return Explained(templated(reused), "ok")
    try:
        answer = await llm.complete_json(deps, prompts.explain_messages(todo, instruction, context), CONTRACT,
                                         validate)
    except llm.LlmError:
        return Explained(templated(reused), "template")
    if answer["status"] == prompts.OFF_TOPIC:
        return Explained(templated(reused), prompts.OFF_TOPIC)
    by_id = {str(e.get("id")): e for e in answer["explanations"] if isinstance(e, Mapping)}
    out, replaced = [], 0
    for issue in reused:
        if issue.get("explanation") or issue["id"] not in by_id:
            out.append(issue)
            continue
        checked, ok = _checked(issue, by_id[issue["id"]])
        replaced += 0 if ok else 1
        out.append(checked)
    return Explained(templated(out), "ok", replaced)


def _checked(issue: dict, answer: Mapping) -> tuple[dict, bool]:
    grounding = _grounding(issue)
    text = str(answer.get("explanation") or "").strip()
    ok = bool(text) and len(text.split()) <= MAX_EXPLANATION_WORDS and _grounded(text, grounding)
    causes = [str(c).strip() for c in (answer.get("likely_causes") or []) if isinstance(c, str)]
    kept = [c for c in causes if c and len(c.split()) <= MAX_CAUSE_WORDS and _grounded(c, grounding)][:MAX_CAUSES]
    wanted = answer.get("check_next") if isinstance(answer.get("check_next"), str) else None
    return {**issue, "explanation": text if ok else TEMPLATES.get(issue["kind"], ""), "likely_causes": kept,
            "next_command": commands.choose(issue["kind"], issue, wanted)}, ok


def _grounding(issue: Mapping) -> str:
    parts = [*issue["evidence"], issue["detail"], issue["object"], issue["namespace"], *issue["examples"],
             issue.get("correlation", ""), str(issue["count"])]
    return " ".join(parts).casefold()


def _grounded(text: str, grounding: str) -> bool:
    if _COMMAND.search(text):
        return False
    for token in _TOKEN.findall(text):
        if _specific(token) and token.casefold().rstrip(".:") not in grounding:
            return False
    return True


def _specific(token: str) -> bool:
    """A token that names something (a pod, node, number): it has a digit or a `/`, or it is hyphenated
    and not a common English compound."""
    lowered = token.casefold()
    if any(ch.isdigit() for ch in token) or "/" in token:
        return True
    return "-" in token and lowered not in _PLAIN_HYPHENATED


def key(issue: Mapping) -> str:
    return "|".join([issue["kind"], issue["namespace"], issue["object"], *issue["evidence"]])


def cache_of(issues: Sequence[dict]) -> dict[str, dict]:
    return {key(i): {"explanation": i["explanation"], "likely_causes": i["likely_causes"],
                     "next_command": i["next_command"]} for i in issues[:MAX_EXPLAINED] if i.get("explanation")}
