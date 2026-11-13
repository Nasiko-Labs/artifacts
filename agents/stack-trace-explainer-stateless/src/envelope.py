"""Pure: the reply envelope (`summary`, `items`, `confidence`, `needs_human` + the four catalog fields mirrored
from the primary item). `confidence` and `needs_human` are computed here from the checks, never from the model."""

from __future__ import annotations

from explain import Explained
from trace_parse import has_error_text

LEVELS = ("low", "medium", "high")
NEED_CODE_WINDOW = 12
TRACE_IN_ITEM_CHARS = 6_000


def confidence(ex: Explained) -> str:
    checked = ex.checked
    if ex.trace.frameless or checked is None:
        return "low"
    level = "medium"
    # A chain with app frames in several blocks is NOT capped at medium once pasted code verifies the candidate
    # (the plan's demo upgrades the Java chain to high after the follow-up paste).
    if ex.view is not None and checked.code_quotes >= 1 and ex.repairs == 0 and ex.candidate.origin == "app":
        level = "high"
    if checked.quotes_kept == 0:  # nothing from the paste backs the cause: one level down
        level = LEVELS[max(0, LEVELS.index(level) - 1)]
    return level


def need_code(ex: Explained) -> dict | None:
    """The model's request when it passed the check; else, when no pasted code covers an app candidate, a
    window around its line computed here."""
    frame = ex.candidate.frame
    if frame is None or ex.view is not None or ex.candidate.origin != "app":
        return None
    asked = ex.checked.result.get("need_code") if ex.checked else None
    if asked:
        return asked
    return {"file": frame.file, "lines": f"{max(1, frame.line - NEED_CODE_WINDOW)}-{frame.line + NEED_CODE_WINDOW}"}


def location_detail(ex: Explained) -> dict | None:
    frame = ex.candidate.frame
    if frame is None:
        return None
    return {"file": frame.file, "line": frame.line, "function": frame.func or None, "origin": ex.candidate.origin,
            "from": "code" if ex.view is not None else "trace"}


def item(index: int, ex: Explained) -> dict:
    trace, result = ex.trace, (ex.checked.result if ex.checked else {})
    level = confidence(ex)
    need = need_code(ex)
    checked = ex.checked
    return {
        "trace_index": index, "exception": trace.exception_type, "message": trace.message,
        "language": trace.language, "likely_cause": result.get("likely_cause") or "",
        "location": ex.candidate.frame.label() if ex.candidate.frame else "unknown",
        "location_detail": location_detail(ex), "fix_suggestion": result.get("fix_suggestion") or "",
        "fix_code": result.get("fix_code"), "how_to_verify": list(result.get("how_to_verify") or ()),
        "evidence": list(result.get("evidence") or ()), "chain": trace.chain(), "need_code": need,
        "frame_reason": result.get("frame_reason") if ex.moved else None,
        "confidence_notes": result.get("confidence_notes"), "confidence": level,
        "needs_human": level == "low" or need is not None,
        "checks": {"quotes_removed": checked.quotes_removed if checked else 0,
                   "sentences_removed": checked.sentences_removed if checked else 0,
                   "fix_code_dropped": bool(checked and checked.fix_code_dropped),
                   "repairs": ex.repairs, "repaired_problems": ex.repaired_problems},
        "trace": trace.raw[:TRACE_IN_ITEM_CHARS],
    }


def build(results: list[Explained], primary: int, skipped_traces: int) -> dict:
    """`results` in paste order; `primary` indexes into it. Items list the primary first."""
    order = [primary, *(i for i in range(len(results)) if i != primary)]
    items = [item(i + 1, results[i]) for i in order if results[i].status == "ok"]
    unexplained = [i + 1 for i in order if results[i].status != "ok"]
    if not items:
        return {"summary": "No error could be explained.", "items": [], "confidence": "low", "needs_human": True,
                "likely_cause": "", "location": "unknown", "fix_suggestion": "", "how_to_verify": [],
                "skipped_traces": skipped_traces, "unexplained_traces": unexplained}
    top = items[0]
    first_sentence = top["likely_cause"].split(". ")[0].rstrip(".")
    root = results[primary].trace.root_block
    summary = f"Root cause: {root.exception_type} at {top['location']}. {first_sentence}."
    return {"summary": summary, "items": items, "likely_cause": top["likely_cause"], "location": top["location"],
            "fix_suggestion": top["fix_suggestion"], "how_to_verify": top["how_to_verify"],
            "confidence": top["confidence"], "needs_human": top["needs_human"],
            "skipped_traces": skipped_traces, "unexplained_traces": unexplained}


def not_a_trace_needs_human(text: str) -> bool:
    """The model said "not a trace" for text that does contain an error word: a person should look."""
    return has_error_text(text)
