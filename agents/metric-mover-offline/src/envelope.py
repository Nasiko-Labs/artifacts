"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from code checks, never taken from the model.

confidence: low when the segments disagree with the table's Total row by > 1%, the overall change is within
noise, there are fewer than 5 segments or over 20% of rows were skipped; medium when there are no counts
(additive), a measurement-artifact hypothesis ranks first, a hypothesis was dropped or rows were skipped;
else high. needs_human: confidence low, or the top hypothesis is a measurement artifact."""

from __future__ import annotations

from collections.abc import Sequence

import analysis as an
import prompts
from checks import Checked
from decompose import Breakdown, Seg
from events import Event
from evidence import Evidence
from fmt import fmt, pp, rate, signed

LOW_SEGMENTS = 5
LOW_SKIPPED_SHARE = 0.2
TABLE_SEGMENTS = 10


def confidence(result: an.Analysis, checked: Checked) -> str:
    frame = result.frame
    skipped_share = frame.skipped / max(frame.skipped + len(frame.rows), 1)
    if an.is_mismatch(result) or result.significance == "within noise" or result.segments < LOW_SEGMENTS \
            or skipped_share > LOW_SKIPPED_SHARE:
        return "low"
    top_artifact = bool(checked.hypotheses) and checked.hypotheses[0]["type"] == "measurement_artifact"
    if result.kind == "additive" or top_artifact or checked.dropped or frame.skipped:
        return "medium"
    return "high"


def headline(result: an.Analysis) -> str:
    b = result.top
    name = result.metric[:1].upper() + result.metric[1:]
    verb = "rose" if b.delta > 0 else "fell" if b.delta < 0 else "did not change"
    if result.kind == "rate":
        moved = f"from {rate(b.before)} to {rate(b.after)} ({pp(b.delta)})"
        driver = f" Driven by: rate change ({b.rate_share * 100:.0f}%) and mix ({b.mix_share * 100:.0f}%) by {b.dim}."
    else:
        moved = f"from {fmt(b.before)} to {fmt(b.after)} ({signed(b.delta)})"
        driver = f" The change is {b.conc.label} by {b.dim}."
    return f"{name} {verb} {moved}, within a table of {fmt(result.segments)} segments.{driver}"


def seg_row(b: Breakdown, s: Seg) -> dict:
    row = {"segment": s.name, "before": s.b, "after": s.a, "effect": s.effect, "share_pct": s.share,
           "new": s.new, "vanished": s.vanished}
    if b.kind == "rate":
        test = s.test
        row.update({"n_before": s.n0, "n_after": s.n1, "weight_before": s.w0, "weight_after": s.w1, "mix": s.mix,
                    "rate_effect": s.rate, "z": test.z if test else None,
                    "significance": test.label if test else "not testable"})
    return row


def dimension(b: Breakdown) -> dict:
    return {"dimension": b.dim, "herfindahl": round(b.h, 4), "explained_top3": round(b.explained, 4),
            "concentration": b.conc.label, "driver": b.driver or None, "total_mix": b.total_mix,
            "total_rate": b.total_rate, "segments": [seg_row(b, s) for s in b.segs[:TABLE_SEGMENTS]]}


def totals(result: an.Analysis) -> dict:
    b = result.top
    if result.kind == "rate":
        return {"before": b.before, "after": b.after, "delta_pp": b.delta * 100,
                "delta_pct": b.delta / b.before * 100 if b.before else None}
    return {"before": b.before, "after": b.after, "delta": b.delta,
            "delta_pct": b.delta / b.before * 100 if b.before else None}


def caveats(result: an.Analysis, used: Sequence[Event], ignored: Sequence[Event], window_known: bool) -> list[str]:
    out = []
    if result.kind == "rate":
        out.append("Significance is per segment, without multiple-comparison correction: with many segments some "
                   "will look significant by chance.")
    else:
        out.append("No significance test possible: only totals were given (no counts).")
    if result.top.near_zero:
        out.append("The net change is near zero, so shares are n/a; I report gross movement instead.")
    if an.is_mismatch(result):
        out.append(f"The segments differ from the table's Total row by {result.mismatch_pct:.1f}%.")
    if used and not window_known:
        out.append("The periods have no dates, so I assumed every listed event falls inside the window.")
    if ignored:
        out.append(f"{len(ignored)} listed event(s) fall outside the compared window and were not used.")
    if result.frame.excluded:
        out.append(f"Excluded at your request: {', '.join(result.frame.excluded)}.")
    return out


def build(result: an.Analysis, evidence: Sequence[Evidence], checked: Checked, used: Sequence[Event],
          ignored: Sequence[Event], window_known: bool, notes: Sequence[str] = ()) -> dict:
    level = confidence(result, checked)
    hyps = [{k: v for k, v in h.items() if k != "key"} for h in checked.hypotheses]
    top_artifact = bool(hyps) and hyps[0]["type"] == "measurement_artifact"
    return {
        "summary": headline(result),
        "items": hyps,
        "hypotheses": hyps,
        "metric": result.metric,
        "metric_type": result.kind,
        "periods": list(result.frame.labels),
        "totals": totals(result),
        "dimension_ranking": [dimension(b) for b in result.dims],
        "cross": dimension(result.cross) if result.cross else None,
        "classification": {"concentration": result.top.conc.label, "driver": result.top.driver or None},
        "significance": result.significance,
        "events_used": [e.to_dict() for e in used],
        "evidence": [e.to_dict() for e in evidence],
        "caveats": caveats(result, used, ignored, window_known),
        "method": prompts.RATE_METHOD if result.kind == "rate" else "Contribution c_i = after_i - before_i.",
        "dropped": checked.dropped,
        "added_by_code": checked.added,
        "notes": list(notes),
        "confidence": level,
        "needs_human": level == "low" or top_artifact,
    }
