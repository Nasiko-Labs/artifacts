"""Pure: ranking in code. score = likelihood x impact (low 1, medium 2, high 3, so 1-9); ties go to the story
the user's focus lens raised, then to stories more lenses found independently, then to plan order."""

from __future__ import annotations

from dataclasses import dataclass

from specific import Candidate

LEVEL = {"low": 1, "medium": 2, "high": 3}
LENS_ORDER = ("skeptic", "pragmatist", "critic", "architect")
DEFAULT_COUNT, MIN_COUNT, MAX_COUNT = 7, 5, 7


@dataclass(frozen=True)
class Story:
    candidate: Candidate
    raised_by: tuple[str, ...]

    @property
    def score(self) -> int:
        return score(self.candidate)

    def to_dict(self) -> dict:
        return {"candidate": self.candidate.to_dict(), "raised_by": list(self.raised_by)}

    @staticmethod
    def from_dict(data: dict) -> Story:
        return Story(Candidate.from_dict(data["candidate"]), tuple(data.get("raised_by") or ()))


def score(candidate: Candidate) -> int:
    return LEVEL[candidate.likelihood] * LEVEL[candidate.impact]


def lenses_in_order(lenses) -> tuple[str, ...]:
    wanted = set(lenses)
    return tuple(lens for lens in LENS_ORDER if lens in wanted)


def clamp_count(requested: int | None) -> int:
    if requested is None:
        return DEFAULT_COUNT
    return max(MIN_COUNT, min(MAX_COUNT, requested))


def rank(stories: list[Story] | tuple[Story, ...], count: int = DEFAULT_COUNT, focus: str = "") -> list[Story]:
    """The top `count` stories, best first."""
    def key(story: Story) -> tuple:
        focused = 1 if focus and focus in story.raised_by else 0
        return (-story.score, -focused, -len(story.raised_by), story.candidate.line, story.candidate.id)
    return sorted(stories, key=key)[:count]
