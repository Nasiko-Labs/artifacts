"""Pure: the PRD data model. Immutable; carried between steps of one turn as plain dicts (nothing is stored).

A statement with an empty `basis` is *proposed* (the model's, not the brief's): that is computed here
from the basis, never taken from the model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

FACT_KINDS = ("problem", "user", "goal", "constraint", "metric", "number", "deadline", "solution_idea",
              "exclusion", "question")
SECTIONS = ("problem", "users", "goals", "non_goals", "requirements", "metrics", "open_questions")
STATEMENT_SECTIONS = ("problem", "users", "goals", "non_goals")
PRIORITIES = ("must", "should", "could")
PRIORITY_RANK = {"must": 0, "should": 1, "could": 2}
LIMITS = {"problem": 4, "users": 8, "goals": 5, "non_goals": 5, "requirements": 12, "metrics": 5,
          "open_questions": 12}
BASELINE_PH, TARGET_PH, WINDOW_PH = "[baseline?]", "[target?]", "[window?]"
MAX_PROPOSED_NON_GOALS = 2


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str
    snippet: str


@dataclass(frozen=True)
class Statement:
    text: str
    basis: tuple[str, ...] = ()

    @property
    def proposed(self) -> bool:
        return not self.basis


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    priority: str = "should"
    basis: tuple[str, ...] = ()
    acceptance_hint: str = ""
    from_solution: bool = False
    vague: bool = False

    @property
    def proposed(self) -> bool:
        return not self.basis


@dataclass(frozen=True)
class Metric:
    name: str
    definition: str = ""
    baseline: str = BASELINE_PH
    target: str = TARGET_PH
    window: str = WINDOW_PH
    basis: tuple[str, ...] = ()

    @property
    def proposed(self) -> bool:
        return not self.basis


@dataclass(frozen=True)
class Question:
    question: str
    section: str = "general"
    blocking: bool = False


@dataclass(frozen=True)
class Prd:
    title: str = "Untitled"
    problem: tuple[Statement, ...] = ()
    users: tuple[Statement, ...] = ()
    goals: tuple[Statement, ...] = ()
    non_goals: tuple[Statement, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    metrics: tuple[Metric, ...] = ()
    open_questions: tuple[Question, ...] = ()
    next_req: int = 1
    notes: tuple[str, ...] = field(default=())

    def statements(self) -> list:
        """Every statement-like entry (open questions excluded), for the proposed share."""
        return [*self.problem, *self.users, *self.goals, *self.non_goals, *self.requirements, *self.metrics]

    def proposed_share(self) -> float:
        entries = self.statements()
        return sum(1 for e in entries if e.proposed) / len(entries) if entries else 0.0

    def with_(self, **changes: Any) -> Prd:
        return replace(self, **changes)

    def to_dict(self) -> dict:
        data = asdict(self)
        for name in ("problem", "users", "goals", "non_goals", "requirements", "metrics"):
            for entry in data[name]:
                entry["basis"] = list(entry["basis"])
        return data

    @staticmethod
    def from_dict(data: dict | None) -> Prd:
        d = data or {}

        def statements(name: str) -> tuple[Statement, ...]:
            return tuple(Statement(s["text"], tuple(s.get("basis") or ())) for s in d.get(name) or ())

        return Prd(
            title=str(d.get("title") or "Untitled"),
            problem=statements("problem"), users=statements("users"), goals=statements("goals"),
            non_goals=statements("non_goals"),
            requirements=tuple(Requirement(**{**r, "basis": tuple(r.get("basis") or ())})
                               for r in d.get("requirements") or ()),
            metrics=tuple(Metric(**{**m, "basis": tuple(m.get("basis") or ())}) for m in d.get("metrics") or ()),
            open_questions=tuple(Question(**q) for q in d.get("open_questions") or ()),
            next_req=int(d.get("next_req") or 1), notes=tuple(d.get("notes") or ()),
        )


def facts_to_state(facts: tuple[Fact, ...]) -> list[dict]:
    return [asdict(f) for f in facts]


def facts_from_state(data: list[dict] | None) -> tuple[Fact, ...]:
    return tuple(Fact(**f) for f in data or ())


def req_number(req_id: str) -> int:
    digits = "".join(c for c in req_id if c.isdigit())
    return int(digits) if digits else 0
