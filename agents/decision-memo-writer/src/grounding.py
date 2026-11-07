"""Pure: what the brief supports. A basis snippet counts only when it appears verbatim (normalised) in
the brief; a figure (number, amount, %, month, quarter) counts only when the brief or code produced it."""

from __future__ import annotations

from collections.abc import Iterable

import text as t
from brief import Brief

MIN_SNIPPET_CHARS = 8
JUDGEMENT = "judgement"


class Ground:
    def __init__(self, brief: Brief, computed: Iterable[str] = ()):
        self._norm = t.normalise(brief.grounding_text)
        self._figures = t.figures(brief.grounding_text) | {str(c) for c in computed}

    def snippet_ok(self, snippet: str) -> bool:
        norm = t.normalise(snippet).strip(" .\"'")
        return len(norm) >= MIN_SNIPPET_CHARS and norm in self._norm

    def basis(self, snippet: object) -> str:
        """The snippet when the brief contains it, else "judgement"."""
        value = " ".join(str(snippet or "").split())
        return value if value.casefold() != JUDGEMENT and self.snippet_ok(value) else JUDGEMENT

    def ungrounded(self, text: str) -> list[str]:
        return t.ungrounded(text, self._figures)

    def fill(self, text: str, placeholder: str) -> str:
        return t.replace_ungrounded(text, self._figures, placeholder)
