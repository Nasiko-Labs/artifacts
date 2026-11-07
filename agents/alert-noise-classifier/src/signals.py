"""Pure: the signals of one group, all computed in code from the parsed fires and the note labels.

short = duration under 5 min; auto-resolved = resolved with no ack and no note, or a note saying it
auto-resolved; ack-no-action = acked with no note, or a note labelled no_action (over fires with an ack or a
note); flap = 3+ resolved fires of the same alert and labels (instance included) within 60 min, counted as
episodes; periodic = 4+ fires whose gaps have a coefficient of variation under 0.2; storm child = 80%+ of
this group's fires start within 5 min after a fire of another group on the same service; pages = fires with
a paging severity (every fire, when the paste carries no severity at all)."""

from __future__ import annotations

import bisect
import itertools
import math
import re
import statistics
from collections.abc import Mapping, Sequence

from fires import SHORT_SECS, Fire
from group import Group, display_name

FLAP_WINDOW_SECS = 3600
FLAP_MIN_CYCLES = 3
PERIODIC_MIN_FIRES = 4
PERIODIC_MAX_CV = 0.2
STORM_WINDOW_SECS = 300
STORM_MIN_SHARE = 0.8
OFF_HOURS = frozenset({22, 23, 0, 1, 2, 3, 4, 5, 6})
_AUTO_NOTE = re.compile(r"\bauto[- ]?resol", re.IGNORECASE)
_ESCALATED = re.compile(r"\bescalat\w*|\bincident\b|\bINC-\d+", re.IGNORECASE)


def note_key(note: str) -> str:
    return " ".join((note or "").casefold().split())


def kind_of(fire: Fire, kinds: Mapping[str, str]) -> str:
    return kinds.get(note_key(fire.note), "unclear") if fire.note.strip() else ""


def is_page(fire: Fire, all_page: bool) -> bool:
    return all_page or fire.is_page


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct * len(ordered)) - 1)]


def flap_episodes(fires: Sequence[Fire]) -> int:
    by_labels: dict[tuple, list[Fire]] = {}
    for f in fires:
        if f.is_resolved:
            by_labels.setdefault((f.instance, f.labels), []).append(f)
    episodes = 0
    for members in by_labels.values():
        times = sorted(f.t_fire for f in members)
        i = 0
        while i < len(times):
            j = i
            while j + 1 < len(times) and (times[j + 1] - times[i]).total_seconds() <= FLAP_WINDOW_SECS:
                j += 1
            if j - i + 1 >= FLAP_MIN_CYCLES:
                episodes, i = episodes + 1, j + 1
            else:
                i += 1
    return episodes


def periodic(fires: Sequence[Fire]) -> tuple[bool, float | None]:
    """(periodic, mean gap in seconds)."""
    if len(fires) < PERIODIC_MIN_FIRES:
        return False, None
    times = sorted(f.t_fire for f in fires)
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    mean = statistics.fmean(gaps)
    if mean <= 0:
        return False, None
    return statistics.pstdev(gaps) / mean < PERIODIC_MAX_CV, mean


def storm_parent(g: Group, groups: Sequence[Group]) -> tuple[str, float, tuple[Fire, ...]]:
    """(display name of the parent group, co-occurrence share, the co-occurring fires), or ("", 0.0, ())."""
    if not g.service:
        return "", 0.0, ()
    best, best_share, best_hits = "", 0.0, ()
    for other in groups:
        if other.key == g.key or other.service.casefold() != g.service.casefold():
            continue
        starts = sorted(f.t_fire for f in other.fires)
        hits = tuple(f for f in g.fires if _follows(f.t_fire, starts))
        share = len(hits) / g.n
        if share >= STORM_MIN_SHARE and share > best_share:
            best, best_share, best_hits = display_name(other, groups), share, hits
    return best, round(best_share, 4), best_hits


def _follows(t, starts: Sequence) -> bool:
    """True when some start is strictly before `t` and at most 5 min earlier."""
    at = bisect.bisect_left(starts, t) - 1
    return at >= 0 and (t - starts[at]).total_seconds() <= STORM_WINDOW_SECS


def compute(g: Group, groups: Sequence[Group], kinds: Mapping[str, str], all_page: bool) -> dict:
    fires = g.fires
    durs = [f.duration_s for f in fires if f.duration_s is not None]
    note_kinds = [kind_of(f, kinds) for f in fires]
    auto = sum(1 for f in fires if f.is_resolved and not f.acked and (not f.note.strip() or _AUTO_NOTE.search(f.note)))
    touched = [(f, k) for f, k in zip(fires, note_kinds, strict=True) if f.acked or f.note.strip()]
    ack_none = sum(1 for f, k in touched if k == "no_action" or (f.acked and not k))
    is_periodic, mean_gap = periodic(fires)
    parent, co_share, co_fires = storm_parent(g, groups)
    first, last = fires[0].t_fire, fires[-1].t_fire
    return {
        "n": g.n,
        "with_duration": len(durs),
        "median_dur": statistics.median(durs) if durs else None,
        "p90_dur": percentile(durs, 0.9),
        "short": sum(1 for d in durs if d < SHORT_SECS),
        "short_share": _share(sum(1 for d in durs if d < SHORT_SECS), len(durs)),
        "auto_resolved": auto,
        "auto_resolved_share": _share(auto, g.n),
        "touched": len(touched),
        "ack_no_action": ack_none,
        "ack_no_action_share": _share(ack_none, len(touched)),
        "actions": note_kinds.count("action_taken"),
        "action_share": _share(note_kinds.count("action_taken"), g.n),
        "noted": sum(1 for k in note_kinds if k),
        "no_action_notes": note_kinds.count("no_action"),
        "escalated": any(_ESCALATED.search(f.note) for f in fires if f.note),
        "flap": flap_episodes(fires),
        "periodic": is_periodic,
        "mean_gap_s": mean_gap,
        "off_hours_share": _share(sum(1 for f in fires if f.t_fire.hour in OFF_HOURS), g.n),
        "storm_parent": parent,
        "storm_share": co_share,
        "storm_hits": len(co_fires),
        "storm_hit_pages": sum(1 for f in co_fires if is_page(f, all_page)),
        "pages": sum(1 for f in fires if is_page(f, all_page)),
        "critical": any(f.severity.casefold() in ("critical", "crit", "p1", "sev1", "sev-1") for f in fires),
        "first": first.isoformat(),
        "last": last.isoformat(),
        "span_days": (last.date() - first.date()).days + 1,
    }
