"""Pure: complaint shares of our app against each competitor app, per theme name (theirs folded into ours by
the cluster step). Shares are percent of each app's reviews that carry a complaint; delta = theirs - ours."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from config import Config
from records import CompetitorRow, Review
from stats import theme_counts

SMALL_SAMPLE = "small sample: {n} reviews"
THEY_HAVE_IT = "they have it, we do not hear about it"


def _share(count: int, base: int) -> float:
    return round(100 * count / base, 1) if base else 0.0


def rows(app: str, ours: Sequence[Review], our_rt: Mapping[str, Mapping[str, str]], theirs: Sequence[Review],
         their_rt: Mapping[str, Mapping[str, str]], config: Config) -> list[CompetitorRow]:
    """Themes where either side has at least `theme_min` reviews, the largest gap first."""
    our_counts, their_counts = theme_counts(ours, our_rt), theme_counts(theirs, their_rt)
    our_base = sum(1 for r in ours if r.id in our_rt)
    their_base = sum(1 for r in theirs if r.id in their_rt)
    small = SMALL_SAMPLE.format(n=len(theirs)) if len(theirs) < config.small_sample else ""
    out = []
    for name in sorted(set(our_counts) | set(their_counts)):
        mine, them = our_counts[name], their_counts[name]
        if max(mine, them) < config.theme_min:
            continue
        our_share, their_share = _share(mine, our_base), _share(them, their_base)
        notes = [THEY_HAVE_IT] if them >= config.theme_min and mine == 0 else []
        out.append(CompetitorRow(app=app, theme=name, our_count=mine, our_share=our_share, their_count=them,
                                 their_share=their_share, delta=round(their_share - our_share, 1),
                                 note="; ".join([*notes, small] if small else notes)))
    return sorted(out, key=lambda r: (-abs(r.delta), r.theme))
