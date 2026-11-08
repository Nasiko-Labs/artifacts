"""Pure: the goal -> angle set table. Code picks the three angles; the model writes them. The variants
differ in angle, not just wording, and the angle names are shown so the user can ask for "more like the
problem-first one"."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_GOAL = "announce"


@dataclass(frozen=True)
class Angle:
    name: str
    how: str


ANGLES: dict[str, tuple[Angle, ...]] = {
    "announce": (Angle("Result-first", "open with what changed and who it helps"),
                 Angle("Problem-first", "open with the pain, then the fix"),
                 Angle("Behind-the-scenes", "why it was built and what it took")),
    "educate": (Angle("One tip", "a single lesson, stated plainly, with the reason"),
                Angle("Mistake-to-fix", "a common error, then the correction"),
                Angle("Checklist", "three to five short steps")),
    "opinion": (Angle("Contrarian", "state the belief, then why"),
                Angle("Question-led", "pose a real question the reader cares about, then a view"),
                Angle("Example-led", "a concrete case first, then the take")),
    "story": (Angle("Before/after", "how it was, what changed, how it is now"),
              Angle("Turning point", "the moment that changed the outcome"),
              Angle("Lesson learned", "what happened and the one lesson")),
    "hire": (Angle("Role facts first", "the role and the key facts up front"),
             Angle("Why it matters", "what the person will own and why it matters"),
             Angle("Direct invite", "speak to the right candidate and invite them")),
    "event": (Angle("Event facts first", "what, when and where up front"),
              Angle("Why attend", "what attendees will get out of it"),
              Angle("Direct invite", "a personal invitation to join")),
}


def angles_for(goal: str | None) -> tuple[Angle, ...]:
    return ANGLES.get(goal or DEFAULT_GOAL, ANGLES[DEFAULT_GOAL])


def angle_index(text: str, names: tuple[str, ...]) -> int | None:
    """The variant a user names by its angle ("the problem-first one"), 1-based."""
    lowered = (text or "").casefold().replace("-", " ")
    for index, name in enumerate(names, start=1):
        if name and name.casefold().replace("-", " ") in lowered:
            return index
    return None
