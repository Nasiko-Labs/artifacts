"""Pure: applying the verifier's verdicts to the reviewer's raw comments, before the code checks run.

Comments are addressed by position (`c1` = the first). The verifier can only discard a known comment or lower its
severity; an unknown id, a raised severity or a severity outside the scale is ignored, and a comment with no
verdict is kept as it is. An unusable verifier answer keeps every comment (the code checks still run)."""

from __future__ import annotations

from typing import Any

from severity import RANK


def apply(raw: list, answer: dict | None) -> tuple[list, int]:
    """(the comments that survive the verifier, how many it discarded)."""
    verdicts = _by_id(answer)
    kept: list = []
    discarded = 0
    for n, item in enumerate(raw, start=1):
        verdict = verdicts.get(f"c{n}")
        if verdict is None or not isinstance(item, dict):
            kept.append(item)
            continue
        if verdict.get("keep") is False:
            discarded += 1
            continue
        kept.append(_lowered(item, verdict.get("severity")))
    return kept, discarded


def _by_id(answer: dict | None) -> dict[str, dict]:
    items = (answer or {}).get("verdicts")
    if not isinstance(items, list):
        return {}
    return {str(v.get("id") or "").strip().casefold(): v for v in items if isinstance(v, dict)}


def _lowered(item: dict, severity: Any) -> dict:
    level = str(severity or "").strip().casefold()
    current = str(item.get("severity") or "").strip().casefold()
    if level in RANK and current in RANK and RANK[level] > RANK[current]:
        return {**item, "severity": level}
    return item
