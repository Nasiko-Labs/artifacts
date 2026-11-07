"""The one model step of a review: label each alert symptom / cause / unclear from its name, severity,
annotations and expression. Batches of at most 30, three at a time, one repair per batch. Unknown ids are
ignored, a missing id is `unclear`, a failed batch leaves its alerts unlabelled (AR008 skipped for them)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import prompts
from alerts import Alert
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("alert-rule-reviewer.label")

BATCH_SIZE = 30
CONCURRENCY = 3
EXPR_MAX_CHARS = 300
TEXT_MAX_CHARS = 200
WHY_MAX_WORDS = 15
KINDS = ("symptom", "cause", "unclear")


@dataclass
class Labels:
    kinds: dict[str, str] = field(default_factory=dict)     # alert id -> symptom|cause|unclear
    why: dict[str, str] = field(default_factory=dict)
    failed: set[str] = field(default_factory=set)            # ids whose batch failed
    deadline_hit: bool = False

    def counts(self) -> dict[str, int]:
        return {kind: sum(1 for k in self.kinds.values() if k == kind) for kind in KINDS}


def row(alert: Alert) -> dict:
    text = alert.annotations.get("summary") or alert.annotations.get("description") or str(
        alert.fields.get("message") or "")
    return {"id": alert.id, "name": alert.name, "severity": alert.severity or alert.fields.get("priority"),
            "summary": text[:TEXT_MAX_CHARS], "expression": alert.expr[:EXPR_MAX_CHARS]}


def batches(alerts: Sequence[Alert]) -> list[list[Alert]]:
    return [list(alerts[i:i + BATCH_SIZE]) for i in range(0, len(alerts), BATCH_SIZE)]


def _validator(ids: set[str]):
    def validate(data) -> dict | None:
        if not isinstance(data, dict):
            return None
        if data.get("status") == "off_topic":
            return {"off_topic": True, "labels": {}}
        items = data.get("labels")
        if not isinstance(items, list):
            return None
        labels = {}
        for item in items:
            if isinstance(item, dict) and item.get("id") in ids and item.get("kind") in KINDS:
                why = " ".join(str(item.get("why") or "").split()[:WHY_MAX_WORDS])
                labels[item["id"]] = (item["kind"], why)
        return {"labels": labels}
    return validate


async def label(deps: TurnDeps, alerts: Sequence[Alert], policy: Sequence[str]) -> Labels:
    result = Labels()
    if not alerts:
        return result
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(batch: list[Alert]) -> None:
        ids = {a.id for a in batch}
        async with gate:
            try:
                answer = await complete_json(deps, prompts.label_messages([row(a) for a in batch], policy),
                                             prompts.LABEL_CONTRACT, _validator(ids))
            except LlmError as e:
                log.info("label batch failed: %s", e)
                result.deadline_hit |= not deps.has_time()
                result.failed |= ids
                return
        if answer.get("off_topic"):
            result.failed |= ids
            return
        for alert_id in ids:
            kind, why = answer["labels"].get(alert_id, ("unclear", ""))
            result.kinds[alert_id], result.why[alert_id] = kind, why

    await asyncio.gather(*(one(b) for b in batches(alerts)))
    return result
