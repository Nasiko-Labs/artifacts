"""Pure data: a review, a verified complaint, and the rows the stats produce. All frozen; state keeps the
dict forms (`to_dict` / `review_from`)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

UNKNOWN_VERSION = "unknown"


@dataclass(frozen=True)
class Review:
    id: str
    app: str
    body: str
    rating: int | None = None
    version: str = UNKNOWN_VERSION
    date: str | None = None   # ISO yyyy-mm-dd
    store: str | None = None  # "ios" / "android"
    title: str = ""
    source: str = "pasted"    # "pasted" / "fetched"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def text(self) -> str:
        """What a quote may come from: the title and the body."""
        return f"{self.title}\n{self.body}" if self.title else self.body


_REVIEW_FIELDS = tuple(f.name for f in fields(Review))


def review_from(data: Mapping[str, Any]) -> Review:
    return Review(**{k: data[k] for k in _REVIEW_FIELDS if k in data})


@dataclass(frozen=True)
class Complaint:
    review_id: str
    label: str
    quote: str


@dataclass(frozen=True)
class Theme:
    name: str
    app: str
    count: int
    share: float
    avg_rating: float | None
    versions: Mapping[str, int] = field(default_factory=dict)
    quotes: tuple[str, ...] = ()
    new_in: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "app": self.app, "count": self.count, "share": self.share,
                "avg_rating": self.avg_rating, "versions": dict(self.versions), "quotes": list(self.quotes),
                "new_in": self.new_in or None}


@dataclass(frozen=True)
class VersionRow:
    version: str
    reviews: int
    rated: int
    avg_rating: float | None
    low_share: float | None
    top_complaints: tuple[str, ...] = ()
    flag: str = ""

    def to_dict(self) -> dict:
        return {"version": self.version, "reviews": self.reviews, "rated": self.rated, "avg_rating": self.avg_rating,
                "low_share": self.low_share, "top_complaints": list(self.top_complaints), "flag": self.flag or None}


@dataclass(frozen=True)
class CompetitorRow:
    app: str
    theme: str
    our_count: int
    our_share: float
    their_count: int
    their_share: float
    delta: float
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
