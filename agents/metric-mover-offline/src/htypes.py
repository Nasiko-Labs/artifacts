"""Pure: the hypothesis types (enum, display names, definitions), the how-to-confirm library, and the
default wording code uses when it must add a hypothesis itself. Named `htypes` so it never shadows the
standard library's `types` module."""

from __future__ import annotations

TYPES = ("segment_concentrated", "mix_shift", "rate_change_within_segment", "broad_based", "event_aligned",
         "new_or_vanished_segment", "measurement_artifact")
LABELS = {
    "segment_concentrated": "Concentrated in a segment",
    "mix_shift": "Mix shift",
    "rate_change_within_segment": "Rate change within a segment",
    "broad_based": "Broad-based change",
    "event_aligned": "Aligned with an event",
    "new_or_vanished_segment": "New or vanished segment",
    "measurement_artifact": "Measurement artifact",
}
DEFINITIONS = {
    "segment_concentrated": "the change sits in one or two segments.",
    "mix_shift": "the composition (share of volume per segment) changed more than behaviour did.",
    "rate_change_within_segment": "behaviour (the rate) changed inside one or more segments.",
    "broad_based": "the change is spread across many segments; say that a systemic or global cause is more likely "
                   "than a local one.",
    "event_aligned": "an event the user listed falls in the window and matches the segments that moved; cite its "
                     "event id.",
    "new_or_vanished_segment": "a segment appeared or disappeared between the periods.",
    "measurement_artifact": "a tracking or definition change, small samples or data gaps may explain part of it.",
}
LIBRARY = {
    "segment_concentrated": "Pull the metric for {segment} alone, day by day, and look for the day it changed.",
    "mix_shift": "Compare the volume by {dimension} before and after; check what changed in acquisition or traffic "
                 "sources.",
    "rate_change_within_segment": "Compare cohorts inside {segment} (new vs returning, device, version) before and "
                                  "after.",
    "broad_based": "Check for a global cause: a release, an outage, a pricing or tracking change, a seasonal "
                   "pattern.",
    "event_aligned": "Compare the metric the day before and after {event_date}; check whether segments not touched "
                     "by the event moved.",
    "new_or_vanished_segment": "Check whether {segment} was renamed, merged or stopped being recorded.",
    "measurement_artifact": "Check the tracking/instrumentation changelog and the raw event counts for these "
                            "segments.",
}
DEFAULT_TEXT = {
    "segment_concentrated": "The change may be concentrated in {dimension} = {segment}.",
    "mix_shift": "The mix by {dimension} may have shifted (volume moved toward or away from {segment}) more than "
                 "behaviour inside segments changed.",
    "rate_change_within_segment": "Behaviour may have changed inside {dimension} = {segment}: its own rate moved.",
    "broad_based": "The change is spread across segments: a systemic or global cause is more likely than a local "
                   "one.",
    "event_aligned": "The change may be linked to the listed event: {event}.",
    "new_or_vanished_segment": "{dimension} = {segment} {appeared}, which may be a real change or a recording "
                               "change.",
    "measurement_artifact": "Part of the move may be a measurement artifact (tracking, definitions, small samples "
                            "or data gaps).",
}


def order(kind: str) -> int:
    return TYPES.index(kind) if kind in TYPES else len(TYPES)


def fill(template: str, **values: str) -> str:
    """str.format that leaves an unknown placeholder visible instead of failing."""
    class _Keep(dict):
        def __missing__(self, key: str) -> str:
            return f"<{key}?>"

    return template.format_map(_Keep(values))


def label(kind: str) -> str:
    return LABELS.get(kind, kind)
