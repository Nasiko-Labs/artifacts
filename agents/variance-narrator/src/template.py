"""Pure: the code-template narrative, built only from digest strings. Used for zero-call re-renders and
whenever the model's narrative fails its checks, so the agent stays useful when the model misbehaves."""

from __future__ import annotations

from digest import LENGTHS, Digest, Line
from variance import FLAT

DRIVER_LINES = {"short": 1, "board": 2, "default": 3}
MAX_LISTED = 5
NO_DRIVER = "driver not provided"


def headline(d: Digest) -> str:
    period = f" for {d.period}" if d.period else ""
    if d.analysis.totals.direction == FLAT:
        return f"Total actual of {d.total_actual} was on budget of {d.total_budget}{period}."
    pct = f" ({d.total_pct})" if d.analysis.totals.variance_pct is not None else ""
    return (f"Total actual of {d.total_actual} was {d.total_amount}{pct} {d.total_word} budget of "
            f"{d.total_budget}{period}, {d.total_verdict}.")


def driver_sentence(ln: Line) -> str:
    pct = f" ({ln.pct})" if ln.has_pct else " (no budget set)"
    driver = f"driver given: {ln.item.driver.rstrip('.')}" if ln.item.driver else NO_DRIVER
    return f"{ln.item.name} was {ln.amount}{pct} {ln.word} budget, {ln.verdict}; {driver}."


def offset_sentence(offsets: tuple[Line, ...]) -> str:
    parts = [f"{ln.item.name} ({ln.amount}{', ' + ln.pct if ln.has_pct else ''} {ln.word} budget)" for ln in offsets]
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Partly offset by {joined}."


def still_to_explain(names: tuple[str, ...]) -> str:
    if not names:
        return ""
    shown = ", ".join(names[:MAX_LISTED])
    more = f" and {len(names) - MAX_LISTED} more" if len(names) > MAX_LISTED else ""
    return f"Still to explain: {shown}{more}."


def driver_lines(d: Digest) -> tuple[Line, ...]:
    direction = d.analysis.totals.direction
    with_total = tuple(ln for ln in d.lines if direction == FLAT or ln.item.direction == direction)
    return with_total or d.lines


def narrative(d: Digest) -> str:
    sentences = [headline(d)]
    lines = driver_lines(d)[:DRIVER_LINES.get(d.length, 3)]
    if not d.lines:
        sentences.append("No single line crossed the materiality threshold.")
    sentences += [driver_sentence(ln) for ln in lines]
    if d.length != "short":
        offsets = tuple(ln for ln in d.offsets if ln not in lines)
        sentences += [offset_sentence(offsets), still_to_explain(d.unexplained)]
    return fit([s for s in sentences if s], LENGTHS[d.length][1])


def fit(sentences: list[str], max_words: int) -> str:
    """Drops sentences from the end (never the headline) until the text is within `max_words`."""
    kept = list(sentences)
    while len(kept) > 1 and sum(len(s.split()) for s in kept) > max_words:
        kept.pop()
    return " ".join(kept)
