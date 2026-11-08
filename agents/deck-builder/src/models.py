"""The deck spec: what the model returns (validated here) and what the builder, renderer, script export
and checkpoint all share. Chart values are never part of the model's output: a chart slide names a parsed
table and its columns; the numbers are read from that table in code."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

log = logging.getLogger("deck_builder.models")

Layout = Literal["title", "bullets", "two_column", "chart", "table", "big_number", "quote", "closing"]
LAYOUTS: tuple[str, ...] = ("title", "bullets", "two_column", "chart", "table", "big_number", "quote", "closing")
FRAME_LAYOUTS = frozenset({"title", "closing"})


class _NoNulls(BaseModel):
    """A null from the model means "not given": the field keeps its default."""

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        return {k: v for k, v in data.items() if v is not None} if isinstance(data, dict) else data


class Fact(_NoNulls):
    source: str = Field(default="", alias="from")

    model_config = {"populate_by_name": True}


class ChartRef(_NoNulls):
    table: str = "T1"
    label_column: str = ""
    value_columns: list[str] = Field(default_factory=list)
    kind: str = ""          # a request ("pie"); the type is decided in code


class TableRef(_NoNulls):
    table: str = "T1"
    columns: list[str] = Field(default_factory=list)
    rows_from: int = 0      # set in code when a long table is split over slides
    rows_to: int | None = None


class Stat(_NoNulls):
    value: str = ""
    caption: str = ""


class Slide(BaseModel):
    layout: Layout = "bullets"
    title: str = ""
    bullets: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)      # two_column: the second column
    notes: str = ""
    chart: ChartRef | None = None
    table: TableRef | None = None
    stat: Stat | None = None
    quote: str = ""
    facts: list[Fact] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data: Any) -> Any:
        """The model's JSON is untidy: nulls for strings, facts as plain strings, refs that are not objects."""
        if not isinstance(data, dict):
            return data
        clean = {k: ("" if v is None and k in ("title", "notes", "quote") else v) for k, v in data.items()}
        for key in ("chart", "table", "stat"):
            if not isinstance(clean.get(key), (dict, BaseModel)):
                clean[key] = None
        if isinstance(clean.get("stat"), dict):
            clean["stat"] = {k: str(v or "") for k, v in clean["stat"].items() if k in ("value", "caption")}
        if isinstance(clean.get("chart"), dict) and isinstance(clean["chart"].get("value_columns"), str):
            clean["chart"] = {**clean["chart"], "value_columns": [clean["chart"]["value_columns"]]}
        facts = clean.get("facts") if isinstance(clean.get("facts"), list) else []
        clean["facts"] = [{"from": f} if isinstance(f, str) else {"from": str(f.get("from") or "")}
                          for f in facts if isinstance(f, (str, dict))]
        return clean

    @field_validator("bullets", "right", mode="before")
    @classmethod
    def _strings(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()]

    @field_validator("layout", mode="before")
    @classmethod
    def _layout(cls, value: object) -> str:
        return value if value in LAYOUTS else "bullets"


class Theme(_NoNulls):
    accent: str = "0072B2"
    second: str = "E69F00"
    third: str = "009E73"


class Deck(BaseModel):
    title: str = "Untitled deck"
    subtitle: str | None = None
    audience: str | None = None
    slides: list[Slide] = Field(default_factory=list)
    theme: Theme = Field(default_factory=Theme)

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        clean = {k: v for k, v in data.items() if v is not None or k in ("subtitle", "audience")}
        if not isinstance(clean.get("slides"), list):
            clean["slides"] = []
        clean["slides"] = [s for s in clean["slides"] if isinstance(s, dict)]
        if not isinstance(clean.get("theme"), dict):
            clean.pop("theme", None)
        for key in ("subtitle", "audience"):
            if clean.get(key) is not None and not isinstance(clean[key], str):
                clean[key] = str(clean[key])
        return clean

    def dump(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


def parse_deck(data: object) -> Deck | None:
    """A Deck from the model's JSON (or a checkpoint), or None when it is not one."""
    if not isinstance(data, dict):
        return None
    try:
        deck = Deck.model_validate(data)
    except ValidationError as e:
        log.warning("deck JSON rejected: %d errors, first at %s", e.error_count(), e.errors()[0].get("loc"))
        return None
    return deck if deck.slides else None
