"""Pure: the score. value = w_impact*impact + w_urgency*urgency + w_effort*(6 - effort), each factor 1-5;
score = round(100 * (value - 1) / 4), clamped 0-100. Presets only swap the weights."""

from __future__ import annotations

import math

DEFAULT = "default"
PRESETS: dict[str, tuple[float, float, float]] = {
    DEFAULT: (0.40, 0.35, 0.25),
    "urgency": (0.25, 0.55, 0.20),
    "impact": (0.60, 0.20, 0.20),
    "quick_wins": (0.25, 0.25, 0.50),
    "equal": (1 / 3, 1 / 3, 1 / 3),
}
PRESET_NAMES = {DEFAULT: "default", "urgency": "urgency first", "impact": "impact first",
                "quick_wins": "quick wins first", "equal": "equal weights"}
_EPSILON = 1e-9


def weights_for(preset: str) -> tuple[float, float, float]:
    return PRESETS.get(preset, PRESETS[DEFAULT])


def _clamp(value: int, low: int = 1, high: int = 5) -> int:
    return max(low, min(high, value))


def score(impact: int, urgency: int, effort: int, weights: tuple[float, float, float]) -> int:
    w_impact, w_urgency, w_effort = weights
    value = w_impact * _clamp(impact) + w_urgency * _clamp(urgency) + w_effort * (6 - _clamp(effort))
    raw = 100 * (value - 1) / 4
    return max(0, min(100, math.floor(raw + 0.5 + _EPSILON)))


def label(preset: str) -> str:
    w_impact, w_urgency, w_effort = weights_for(preset)
    return f"impact {round(w_impact * 100)}, urgency {round(w_urgency * 100)}, effort {round(w_effort * 100)}"


def formula(preset: str) -> str:
    w_impact, w_urgency, w_effort = weights_for(preset)
    return (f"score = 100 x (value - 1) / 4, where value = {w_impact:.2f} x impact + {w_urgency:.2f} x urgency + "
            f"{w_effort:.2f} x (6 - effort)")
