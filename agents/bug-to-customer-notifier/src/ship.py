"""Pure: did the fix ship? Decided here from the ship step's reported evidence, never by the model.

    shipped = tag_found AND not draft AND not prerelease AND published <= now
              AND tracker_released is not False
              AND (no compare result, or compare_status in {behind, identical})

`tag_found`: a release/tag whose name equals the fix version after casefolding and stripping one
leading `v` (`v4.2.0 == 4.2.0`), and whose name and link both appear in this turn's tool output.
Compare is base = the tag, head = the fix commit: `behind`/`identical` mean the fix is in the tag."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from checks import appears_in, normalise_text
from dates import parse_instant
from records import ShipEvidence, ShipVerdict

SHIPPED = "shipped"
MERGED_NOT_RELEASED = "merged_not_released"
TRACKER_ONLY = "released_in_tracker_only"
UNVERIFIED = "unverified"
FIX_IN_TAG = frozenset({"behind", "identical"})


def normalise_version(name: str) -> str:
    text = (name or "").strip().casefold()
    return text[1:] if text.startswith("v") and text[1:2].isdigit() else text


def unverified(reason: str, version: str = "") -> ShipVerdict:
    return ShipVerdict(UNVERIFIED, version=version, reason=reason)


def decide(versions: Sequence[str], evidence: Sequence[ShipEvidence], seen_text: str, now: datetime) -> ShipVerdict:
    """The first fix version that shipped wins; otherwise the first version's verdict and why."""
    seen = normalise_text(seen_text)
    by_version = {normalise_version(e.version): e for e in evidence}
    verdicts = [_one(v, by_version.get(normalise_version(v)), seen, now) for v in versions]
    if not verdicts:
        return unverified("the bug has no fix version to check")
    return next((v for v in verdicts if v.shipped), verdicts[0])


def _one(version: str, ev: ShipEvidence | None, seen: str, now: datetime) -> ShipVerdict:
    if ev is None:
        return ShipVerdict(MERGED_NOT_RELEASED, version, reason=f"no release or tag named {version} was found")
    if not _tag_found(version, ev, seen):
        if ev.tracker_released is True:
            return ShipVerdict(TRACKER_ONLY, version,
                               reason=f"the tracker marks {version} released, but no release or tag with that name "
                                      "was found on the code host")
        return ShipVerdict(MERGED_NOT_RELEASED, version, reason=f"no release or tag named {version} was found")
    problem = _not_published(ev, now)
    if problem:
        return ShipVerdict(MERGED_NOT_RELEASED, version, reason=problem, tag_url=ev.tag_url)
    published = parse_instant(ev.tag_published_at)
    return ShipVerdict(SHIPPED, version, release_date=published.date().isoformat(),
                       reason=f"release {ev.tag_name} was published on {published.date().isoformat()}",
                       tag_url=ev.tag_url)


def _tag_found(version: str, ev: ShipEvidence, seen: str) -> bool:
    named = bool(ev.tag_name) and normalise_version(ev.tag_name) == normalise_version(version)
    return named and appears_in(ev.tag_name, seen) and appears_in(ev.tag_url, seen)


def _not_published(ev: ShipEvidence, now: datetime) -> str:
    published = parse_instant(ev.tag_published_at)
    if ev.is_draft:
        return f"release {ev.tag_name} is still a draft"
    if ev.is_prerelease:
        return f"release {ev.tag_name} is a pre-release"
    if published is None:
        return f"release {ev.tag_name} has no readable publish date"
    if published > now:
        return f"release {ev.tag_name} is dated in the future"
    if ev.tracker_released is False:
        return f"the tracker still marks {ev.version} unreleased"
    if ev.compare_status is not None and ev.compare_status not in FIX_IN_TAG:
        return f"the fix commit is not inside {ev.tag_name} (compare: {ev.compare_status})"
    return ""
