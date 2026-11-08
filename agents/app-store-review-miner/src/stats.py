"""Pure: every number in the report. Themes per app (count, share, average rating, versions, quotes), the
per-version table with regression flags, and complaints new in a version. Nothing here calls a model."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

import versions
from config import Config
from records import UNKNOWN_VERSION, Review, Theme, VersionRow

OTHER_ROW = "other (under {n} reviews each)"
NOT_ENOUGH = "not enough reviews to compare"
LOW_RATING = 2


def review_themes(complaints: Mapping[str, Sequence[Mapping]], label_theme: Mapping[str, str],
                  reviews: Sequence[Review]) -> dict[str, dict[str, str]]:
    """review id → {theme: first quote}, for the given reviews only."""
    out: dict[str, dict[str, str]] = {}
    for review in reviews:
        themes: dict[str, str] = {}
        for item in complaints.get(review.id, ()):
            theme = label_theme.get(item["label"], item["label"])
            themes.setdefault(theme, item["quote"])
        if themes:
            out[review.id] = themes
    return out


def avg_rating(reviews: Sequence[Review]) -> float | None:
    rated = [r.rating for r in reviews if r.rating is not None]
    return round(sum(rated) / len(rated), 2) if rated else None


def low_share(reviews: Sequence[Review]) -> float | None:
    rated = [r.rating for r in reviews if r.rating is not None]
    return round(100 * sum(1 for x in rated if x <= LOW_RATING) / len(rated), 1) if rated else None


def theme_counts(reviews: Sequence[Review], rt: Mapping[str, Mapping[str, str]]) -> Counter:
    return Counter(t for r in reviews for t in rt.get(r.id, {}))


def _quotes(name: str, members: Sequence[Review], rt: Mapping[str, Mapping[str, str]], n: int) -> tuple[str, ...]:
    ordered = sorted(members, key=lambda r: (r.rating is None, r.rating or 0))
    return tuple(dict.fromkeys(rt[r.id][name] for r in ordered))[:n]


def themes(app: str, reviews: Sequence[Review], rt: Mapping[str, Mapping[str, str]],
           config: Config) -> tuple[list[Theme], list[dict]]:
    """Themes with at least `theme_min` reviews (count desc, then name) and the ones below the bar."""
    with_complaints = sum(1 for r in reviews if r.id in rt) or 1
    counts = theme_counts(reviews, rt)
    kept, below = [], []
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if count < config.theme_min:
            below.append({"name": name, "count": count, "app": app})
            continue
        members = [r for r in reviews if name in rt.get(r.id, {})]
        by_version = Counter(r.version for r in members)
        kept.append(Theme(name=name, app=app, count=count, share=round(100 * count / with_complaints, 1),
                          avg_rating=avg_rating(members), quotes=_quotes(name, members, rt, config.max_quotes),
                          versions={v: by_version[v] for v in versions.newest_first(by_version)}))
    return kept, below


def _flag(v: str, by: Mapping[str, Sequence[Review]], config: Config) -> str:
    prev = versions.next_older(v, by)
    if prev is None:
        return ""
    cur, old = by[v], by[prev]
    if min(sum(r.rating is not None for r in cur), sum(r.rating is not None for r in old)) < config.regression_min_rated:
        return NOT_ENOUGH
    low_v, low_p, avg_v, avg_p = low_share(cur), low_share(old), avg_rating(cur), avg_rating(old)
    if round(low_v - low_p, 1) >= config.regression_low_points or round(avg_p - avg_v, 2) >= config.regression_avg_drop:
        return f"regression suspected (vs {prev}: low {low_p:g}% to {low_v:g}%, avg {avg_p:g} to {avg_v:g})"
    return ""


def _row(version: str, members: Sequence[Review], rt: Mapping, names: Sequence[str], flag: str) -> VersionRow:
    counts = theme_counts(members, rt)
    top = tuple(n for n, _ in sorted(((n, counts[n]) for n in names if counts[n]), key=lambda kv: (-kv[1], kv[0])))
    return VersionRow(version=version, reviews=len(members), rated=sum(r.rating is not None for r in members),
                      avg_rating=avg_rating(members), low_share=low_share(members), top_complaints=top[:3], flag=flag)


def version_rows(reviews: Sequence[Review], rt: Mapping[str, Mapping[str, str]], names: Sequence[str],
                 config: Config) -> list[VersionRow]:
    """Newest version first; versions under `min_version_reviews` merged into one row; `unknown` last."""
    by: dict[str, list[Review]] = {}
    for review in reviews:
        by.setdefault(review.version, []).append(review)
    rows, small = [], []
    for v in versions.newest_first(by):
        if v != UNKNOWN_VERSION and len(by[v]) < config.min_version_reviews:
            small.extend(by[v])
        elif v != UNKNOWN_VERSION:
            rows.append(_row(v, by[v], rt, names, _flag(v, by, config)))
    if small:
        rows.append(_row(OTHER_ROW.format(n=config.min_version_reviews), small, rt, names, ""))
    if UNKNOWN_VERSION in by:
        rows.append(_row(UNKNOWN_VERSION, by[UNKNOWN_VERSION], rt, names, ""))
    return rows


def new_in(reviews: Sequence[Review], rt: Mapping[str, Mapping[str, str]], names: Sequence[str],
           config: Config) -> dict[str, str]:
    """theme → the newest version V where it has >= `new_theme_min` reviews and none in the version before
    (which needs at least `min_version_reviews` reviews, else the absence means nothing)."""
    by: dict[str, Counter] = {}
    by_count: dict[str, Counter] = {}
    for review in reviews:
        by.setdefault(review.version, Counter()).update(rt.get(review.id, {}).keys())
        by_count.setdefault(review.version, Counter())["reviews"] += 1
    out: dict[str, str] = {}
    for v in versions.newest_first(by):
        prev = versions.next_older(v, by)
        if prev is None or sum(by_count[prev].values()) < config.min_version_reviews:
            continue
        for name in names:
            if name not in out and by[v][name] >= config.new_theme_min and by[prev][name] == 0:
                out[name] = v
    return out
