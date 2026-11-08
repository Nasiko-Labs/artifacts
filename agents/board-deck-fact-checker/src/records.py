"""The data model: claims from the deck, cells from the sheets, the model's proposed match, and the
code-made comparison. Every record is JSON-safe through `to_dict`/`from_dict` so it can be kept in the
checkpointer between turns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import figures

KINDS = ("absolute", "percent", "growth", "ratio", "count")
BASES = ("relative", "points")
OPS = ("growth", "ratio", "sum", "difference")


@dataclass(frozen=True)
class Claim:
    claim_id: str
    slide: int
    quote: str
    label: str
    value_text: str
    period: str = ""
    kind: str = "absolute"
    basis: str | None = None

    @property
    def number(self) -> figures.Number:
        parsed = figures.parse(self.value_text)
        if parsed is None:  # checks.py never keeps such a claim
            raise ValueError(f"claim {self.claim_id} has no number")
        return parsed

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Claim:
        return Claim(**{k: data[k] for k in Claim.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class Cell:
    cell_id: str
    spreadsheet: str
    tab: str
    address: str           # Tab!C14
    value: float           # absolute, after the cell's own suffix (not the label scale)
    text: str              # the cell exactly as the tool returned it
    row_label: str = ""
    col_label: str = ""
    url: str = ""
    scale: float = 1.0     # from a label such as "($000)"; 1 when none was found
    scale_note: str = ""   # which label set the scale
    scale_known: bool = False
    percent: bool = False  # the cell itself was written as a percentage

    @property
    def source_value(self) -> float:
        return self.value * self.scale

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Cell:
        return Cell(**{k: data[k] for k in Cell.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class Match:
    """The model's proposal for one claim, already validated against that claim's shortlist."""

    claim_id: str
    cell_ids: tuple[str, ...] = ()
    op: str | None = None
    operands: tuple[str, ...] = ()
    reason: str = ""
    note: str = ""  # why the claim has no usable match (code-set)

    def to_dict(self) -> dict:
        return {**asdict(self), "cell_ids": list(self.cell_ids), "operands": list(self.operands)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Match:
        return Match(claim_id=str(data["claim_id"]), cell_ids=tuple(data.get("cell_ids") or ()),
                     op=data.get("op"), operands=tuple(data.get("operands") or ()),
                     reason=str(data.get("reason") or ""), note=str(data.get("note") or ""))


@dataclass(frozen=True)
class Comparison:
    claim: Claim
    status: str  # ok | ok_rounded | mismatch | unit_mismatch | period_mismatch | ambiguous | unverifiable
    deck_value: str
    source_value: str = ""
    source_ref: str = ""
    source_url: str = ""
    delta: str = ""
    relative_delta: float | None = None
    materiality: str = ""
    rule_note: str = ""
    warnings: tuple[str, ...] = field(default=())

    def entry(self) -> dict:
        """One `mismatches[]` / `unverifiable[]` item of the envelope."""
        return {"slide": self.claim.slide, "claim_quote": self.claim.quote, "label": self.claim.label,
                "period": self.claim.period, "deck_value": self.deck_value, "source_value": self.source_value,
                "source_ref": self.source_ref, "source_url": self.source_url, "delta": self.delta,
                "relative_delta": self.relative_delta, "status": self.status, "materiality": self.materiality,
                "rule_note": self.rule_note, "warnings": list(self.warnings)}
