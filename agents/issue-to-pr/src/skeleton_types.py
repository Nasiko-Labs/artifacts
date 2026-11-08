"""The skeleton record: one declaration with its 1-based inclusive line span, computed by code."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    start: int
    end: int
    signature: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Symbol:
        return Symbol(str(data["name"]), str(data["kind"]), int(data["start"]), int(data["end"]),
                      str(data.get("signature") or ""))
