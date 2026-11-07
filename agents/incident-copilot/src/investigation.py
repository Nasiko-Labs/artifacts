"""Pure: the fixed facts one investigation runs on (incident, start time, service, repository), JSON-safe
so a REFRESH on a later turn reuses the same start time."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from records import Incident, from_dict, to_dict
from windows import Windows, iso, parse_time, windows

_REPO_IN_URL = re.compile(r"^https?://[^/\s]+/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#].*)?$")


@dataclass(frozen=True)
class Investigation:
    incident: Incident
    t0: datetime
    now: datetime
    service: str
    repo: str = ""
    repo_source: str = ""          # "incident" | "message" | "service" | ""
    lookback_min: int = 0          # 0: the default observability window
    alerting: bool = True          # an alerting source gave the incident (else the user gave the start)

    @property
    def windows(self) -> Windows:
        lookback = timedelta(minutes=self.lookback_min) if self.lookback_min else None
        return windows(self.t0, self.now, lookback)

    def at(self, now: datetime) -> Investigation:
        """The same incident and start time on a later turn (a REFRESH)."""
        return replace(self, now=now)

    def to_dict(self) -> dict:
        return {"incident": to_dict(self.incident), "t0": iso(self.t0), "now": iso(self.now), "service": self.service,
                "repo": self.repo, "repo_source": self.repo_source, "lookback_min": self.lookback_min,
                "alerting": self.alerting}

    @staticmethod
    def from_dict(data: Mapping) -> Investigation:
        return Investigation(incident=from_dict(Incident, data["incident"]), t0=parse_time(data["t0"]),
                             now=parse_time(data["now"]), service=str(data.get("service") or ""),
                             repo=str(data.get("repo") or ""), repo_source=str(data.get("repo_source") or ""),
                             lookback_min=int(data.get("lookback_min") or 0), alerting=bool(data.get("alerting", True)))


def repo_from_url(url: str) -> str:
    """`owner/repo` from a code-host link, verbatim segments; "" when the link has no such path."""
    match = _REPO_IN_URL.match((url or "").strip())
    return f"{match.group(1)}/{match.group(2)}" if match else ""
