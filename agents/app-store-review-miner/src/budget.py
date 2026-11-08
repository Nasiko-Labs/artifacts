"""The per-turn model budget: at most `max_calls` LLM calls (extract + cluster + repair + follow-up) and a
wall-clock deadline. Shared by every step of one turn; never persisted."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    max_calls: int
    calls: int = 0
    exhausted: bool = False  # a step wanted a call and was refused: the step-limit salvage ran

    def take(self) -> bool:
        if self.calls >= self.max_calls:
            self.exhausted = True
            return False
        self.calls += 1
        return True
