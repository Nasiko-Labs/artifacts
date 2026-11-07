"""Pure: one pasted change and its stored form. Frozen; every update is `dataclasses.replace`.

`kind` is set by code (classify.py) and only falls to the model when the prefix and labels say nothing.
`sentence` is the checked customer sentence; `fallback` marks one replaced by the cleaned title."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

KINDS = ("new", "improved", "fixed", "security", "deprecated")


@dataclass(frozen=True)
class Change:
    id: str                       # c1, c2 ... in input order
    title: str                    # the change's own words: prefix, key, labels, PR number and URL removed
    ref: str = ""                 # "#412" or "ABC-123"
    body: str = ""                # description / continuation lines
    url: str = ""                 # only a URL that was pasted with this change
    labels: tuple[str, ...] = ()
    prefix: str = ""              # conventional-commit type ("feat"), lower case
    scope: str = ""               # conventional-commit scope ("export")
    kind: str | None = None       # new | improved | fixed | security | deprecated; None = the model decides
    breaking: bool = False
    migration: str = ""           # pasted migration words for a breaking change
    internal: bool = False        # left out of customer notes, counted
    sentence: str = ""
    theme: str = ""
    grounded: bool = False
    fallback: bool = False        # the sentence is the cleaned title
    jargon: bool = False          # a jargon word survived the repair
    dropped: bool = False         # the user said "drop item N"

    def source_text(self) -> str:
        """Everything the user pasted for this change: what a sentence may be grounded in."""
        return " ".join(p for p in (self.prefix, self.scope, self.title, self.body, self.ref, self.url,
                                    " ".join(self.labels), self.migration) if p)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Change:
        names = {f.name for f in fields(Change)}
        values = {k: v for k, v in data.items() if k in names}
        values["labels"] = tuple(str(x) for x in values.get("labels") or ())
        if values.get("kind") not in KINDS:
            values["kind"] = None
        return Change(**values)


def block_of(change: Change) -> str:
    """The notes block an included change is shown in."""
    if change.breaking:
        return "breaking"
    if change.kind == "security":
        return "security"
    return change.kind or "improved"


def included(changes: tuple[Change, ...] | list[Change]) -> list[Change]:
    return [c for c in changes if not c.internal and not c.dropped]
