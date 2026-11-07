"""parsed fires → group → signals → notes (model only for leftovers) → classify → tune → one JSON-safe result
dict (stored by the checkpointer, rendered by render.py, enveloped by envelope.py). Everything but the note
labels is computed in code."""

from __future__ import annotations

from collections.abc import Sequence

import notes as notes_mod
import rules
import signals
import tune
from detect import Parsed
from fires import Fire, fmt_time
from group import Group, display_name, group
from llm import TurnDeps

MIN_ROWS_FOR_LABELS = 5
EVIDENCE_ROWS = 3
EVIDENCE_MAX_CHARS = 200


def _evidence(g: Group, data: str) -> list[dict]:
    rows = []
    for f in g.fires[:EVIDENCE_ROWS]:
        verbatim = bool(f.raw) and f.raw in data
        rows.append({"row": f.row, "text": f.raw[:EVIDENCE_MAX_CHARS] if verbatim else ""})
    return rows


def _group_record(g: Group, groups: Sequence[Group], kinds: dict, all_page: bool, too_few: bool, data: str) -> dict:
    sig = signals.compute(g, groups, kinds, all_page)
    label, fired, reason = rules.classify(sig, too_few)
    return {
        "key": g.key, "name": g.name, "service": g.service, "severity": g.severity,
        "display": display_name(g, groups), "label": label, "rules": list(fired), "reason": reason,
        "signals": sig, "evidence": _evidence(g, data),
        "timeline": [[f.duration_s, signals.is_page(f, all_page)] for f in g.fires],
        "values": [f.value for f in g.fires if f.value is not None],
        "thresholds": sorted({f.threshold for f in g.fires if f.threshold is not None}),
    }


def _period(fires: Sequence[Fire]) -> dict:
    first = min(f.t_fire for f in fires)
    last = max(f.t_fire for f in fires)
    return {"start": fmt_time(first), "end": fmt_time(last), "days": (last.date() - first.date()).days + 1}


def _not_computed(fires: Sequence[Fire], all_page: bool, by_model_failed: int) -> list[str]:
    out = []
    missing = sum(1 for f in fires if f.duration_s is None)
    if missing:
        out.append(f"durations missing for {missing} of {len(fires)} fires ({round(100 * missing / len(fires))}%)")
    if all_page:
        out.append("no severity or urgency in the paste: every fire is counted as a page")
    if by_model_failed:
        out.append(f"{by_model_failed} responder notes could not be labelled and count as unclear")
    return out


async def run(parsed: Parsed, data: str, deps: TurnDeps, context: tuple[str, ...] = ()) -> dict:
    fires = parsed.fires
    all_page = not any(f.severity.strip() for f in fires)
    groups = group(fires)
    labels = await notes_mod.label((f.note for f in fires), deps, context)
    too_few = len(fires) < MIN_ROWS_FOR_LABELS
    records = [_group_record(g, groups, labels.kinds, all_page, too_few, data) for g in groups]
    records.sort(key=lambda r: (-r["signals"]["n"], r["display"].casefold()))
    suggestions = [s for r in records if r["label"] == rules.NOISE for s in tune.suggest(r)]
    noise_fires = sum(r["signals"]["n"] for r in records if r["label"] == rules.NOISE)
    counts = {label: sum(1 for r in records if r["label"] == label)
              for label in (rules.ACTIONABLE, rules.NOISE, rules.UNCLEAR)}
    missing = sum(1 for f in fires if f.duration_s is None)
    return {
        "groups": records,
        "tune": suggestions,
        "totals": {"fires": len(fires), "groups": len(records), **counts, "noise_fires": noise_fires,
                   "noise_share_pct": round(100 * noise_fires / len(fires), 1),
                   "unclear_fires": sum(r["signals"]["n"] for r in records if r["label"] == rules.UNCLEAR),
                   **tune.totals(records, suggestions)},
        "period": _period(fires),
        "not_computed": _not_computed(fires, all_page, labels.unlabelled),
        "checks": {"shape": parsed.shape, "rows_seen": max(parsed.rows_seen, len(fires)),
                   "rows_skipped": parsed.rows_skipped, "durations_missing": missing,
                   "notes_by_model": labels.by_model, "notes_unlabelled": labels.unlabelled,
                   "dropped": labels.unknown_ids, "too_few_rows": too_few, "all_page": all_page},
        "coverage": list(parsed.notes),
    }
