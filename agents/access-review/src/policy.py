"""Pure: the review policy (defaults shown on the card) and the one-line overrides a user can type, in a
`policy:` block or anywhere in the message: `dormant after 60 days`, `max owners 2`,
`admins allowed: SRE, Platform`. Values are clamped in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

DEFAULT_DORMANT_DAYS = 90
MIN_DORMANT_DAYS, MAX_DORMANT_DAYS = 30, 365
DEFAULT_MAX_OWNERS = 3
MIN_OWNERS, MAX_OWNERS = 1, 50
# Word matches, case-insensitive. "it" only as a whole word, so "security" or "editor" never hit it.
DEFAULT_ADMIN_TITLES = ("engineer", "sre", "devops", "platform", "security", "infrastructure", "it", "admin",
                        "cto", "vp engineering")
_DEFAULT_TITLES_LABEL = "engineering/SRE/DevOps/platform/security/infrastructure/IT"

_DORMANT = re.compile(r"\bdormant(?:\s+(?:after|over|at|beyond|>|=|is|:))*\s*(\d{1,4})\s*(?:d\b|days?\b)",
                      re.IGNORECASE)
_DORMANT_ALT = re.compile(r"\bdormant[_ ]days\s*[:=]?\s*(\d{1,4})", re.IGNORECASE)
_OWNERS = re.compile(r"\bmax(?:imum)?\s+(?:of\s+)?(?:owners?\s*[:=]?\s*(\d{1,3})|(\d{1,3})\s+owners?)",
                     re.IGNORECASE)
_TITLES = re.compile(r"\b(?:admins?|admin titles?|allowed admin titles?|privileged titles?)\s+allowed\s*:\s*(.+)$"
                     r"|\ballowed\s+admin\s+titles?\s*:\s*(.+)$|\badmin titles?\s*:\s*(.+)$",
                     re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Policy:
    dormant_days: int = DEFAULT_DORMANT_DAYS
    max_owners: int = DEFAULT_MAX_OWNERS
    admin_titles: tuple[str, ...] = DEFAULT_ADMIN_TITLES

    def to_dict(self) -> dict:
        return {"dormant_days": self.dormant_days, "max_owners": self.max_owners,
                "admin_titles": list(self.admin_titles)}

    @staticmethod
    def from_dict(data: dict | None) -> Policy:
        data = data or {}
        titles = tuple(str(t) for t in data.get("admin_titles") or ()) or DEFAULT_ADMIN_TITLES
        return Policy(_clamp(int(data.get("dormant_days") or DEFAULT_DORMANT_DAYS), MIN_DORMANT_DAYS,
                             MAX_DORMANT_DAYS),
                      _clamp(int(data.get("max_owners") or DEFAULT_MAX_OWNERS), MIN_OWNERS, MAX_OWNERS), titles)

    def describe(self) -> str:
        titles = (_DEFAULT_TITLES_LABEL if self.admin_titles == DEFAULT_ADMIN_TITLES
                  else ", ".join(self.admin_titles))
        return f"dormant after {self.dormant_days} d · max {self.max_owners} owners · admin titles: {titles}"

    def title_allowed(self, title: str) -> bool:
        lowered = f" {title.casefold()} "
        return any(re.search(rf"(?<![a-z]){re.escape(t.casefold())}(?![a-z])", lowered) for t in self.admin_titles)


@dataclass(frozen=True)
class Override:
    policy: Policy
    changed: tuple[str, ...]  # human lines: what the text changed, with clamps stated


def parse(text: str, base: Policy) -> Override:
    """Apply every override found in `text` to `base`."""
    policy, changed = base, ()
    days = _first_int(_DORMANT, text) or _first_int(_DORMANT_ALT, text)
    if days is not None:
        clamped = _clamp(days, MIN_DORMANT_DAYS, MAX_DORMANT_DAYS)
        policy = replace(policy, dormant_days=clamped)
        changed = (*changed, f"dormant after {clamped} days" + (f" (clamped from {days})" if clamped != days else ""))
    owners = _first_int(_OWNERS, text)
    if owners is not None:
        clamped = _clamp(owners, MIN_OWNERS, MAX_OWNERS)
        policy = replace(policy, max_owners=clamped)
        changed = (*changed, f"max {clamped} owners" + (f" (clamped from {owners})" if clamped != owners else ""))
    titles = _titles(text)
    if titles:
        policy = replace(policy, admin_titles=titles)
        changed = (*changed, "admin titles: " + ", ".join(titles))
    return Override(policy, changed)


def _first_int(pattern: re.Pattern, text: str) -> int | None:
    m = pattern.search(text or "")
    if not m:
        return None
    value = next((g for g in m.groups() if g), None)
    return int(value) if value else None


def _titles(text: str) -> tuple[str, ...]:
    m = _TITLES.search(text or "")
    if not m:
        return ()
    raw = next(g for g in m.groups() if g)
    parts = [" ".join(p.strip(" .;").split()) for p in re.split(r",|/|\band\b", raw)]
    return tuple(dict.fromkeys(p for p in parts if p and len(p) <= 40))[:20]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
