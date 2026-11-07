"""Pure: a story's size from the facts the model reported about it. The model never sizes a story.

    points = criteria + 2*touches_external + 2*new_data + 2*permissions + 2*unknowns + max(ui_screens - 1, 0)
    S when points <= 4, M when 5 to 8, L when >= 9. A spike is always S.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

S_MAX_POINTS = 4
M_MAX_POINTS = 8
FLAG_WEIGHT = 2
MAX_CRITERIA_FACT = 10
MAX_UNKNOWNS_FACT = 5
MAX_SCREENS_FACT = 6
DEFAULT_CRITERIA = 3


@dataclass(frozen=True)
class Facts:
    criteria: int = DEFAULT_CRITERIA
    touches_external: bool = False
    new_data: bool = False
    permissions: bool = False
    unknowns: int = 0
    ui_screens: int = 1

    def to_dict(self) -> dict:
        return {"criteria_estimate": self.criteria, "touches_external": self.touches_external,
                "new_data": self.new_data, "permissions": self.permissions, "unknowns": self.unknowns,
                "ui_screens": self.ui_screens}

    @staticmethod
    def from_dict(data: Any) -> Facts:
        """Model-reported facts, clamped; anything missing or malformed takes a neutral default."""
        raw = data if isinstance(data, dict) else {}
        return Facts(criteria=_int(raw.get("criteria_estimate"), DEFAULT_CRITERIA, 1, MAX_CRITERIA_FACT),
                     touches_external=raw.get("touches_external") is True, new_data=raw.get("new_data") is True,
                     permissions=raw.get("permissions") is True,
                     unknowns=_int(raw.get("unknowns"), 0, 0, MAX_UNKNOWNS_FACT),
                     ui_screens=_int(raw.get("ui_screens"), 1, 0, MAX_SCREENS_FACT))

    def union(self, other: Facts) -> Facts:
        """The facts of two stories merged into one."""
        return Facts(criteria=min(self.criteria + other.criteria, MAX_CRITERIA_FACT),
                     touches_external=self.touches_external or other.touches_external,
                     new_data=self.new_data or other.new_data, permissions=self.permissions or other.permissions,
                     unknowns=max(self.unknowns, other.unknowns), ui_screens=max(self.ui_screens, other.ui_screens))


def _int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


def points(facts: Facts) -> int:
    flags = sum(1 for flag in (facts.touches_external, facts.new_data, facts.permissions) if flag)
    return facts.criteria + FLAG_WEIGHT * (flags + facts.unknowns) + max(facts.ui_screens - 1, 0)


def size_of(facts: Facts, is_spike: bool = False) -> str:
    if is_spike:
        return "S"
    score = points(facts)
    if score <= S_MAX_POINTS:
        return "S"
    return "M" if score <= M_MAX_POINTS else "L"
