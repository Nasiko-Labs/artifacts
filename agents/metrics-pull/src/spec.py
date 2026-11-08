"""The query spec: the only thing the model produces. Validated here (pydantic, extra fields forbidden,
operators and functions from a fixed list, `limit` <= 2,000, no expressions), then every column name is
resolved against the header map in code: exact, else case-insensitive, else the one header that starts
with the term. A term matching several headers is ambiguous (asked as a single-select); a term matching
none is reported as missing and its parts of the spec are left out (never nearest-guessed)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_LIMIT = 2000
MAX_CANDIDATES = 10
Op = Literal["=", "!=", ">", ">=", "<", "<=", "in", "contains", "between"]
AggFn = Literal["sum", "avg", "min", "max", "count", "latest"]
Scalar = str | int | float
_EXPRESSION = re.compile(r"^\s*=|[A-Za-z]+\s*\(")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Filter(_Strict):
    column: str
    op: Op
    value: Scalar | list[Scalar]

    @field_validator("value")
    @classmethod
    def _no_expressions(cls, value: Any) -> Any:
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and _EXPRESSION.search(item):
                raise ValueError("expressions are not allowed; use plain values")
        return value


class Agg(_Strict):
    column: str
    fn: AggFn


class Sort(_Strict):
    column: str
    desc: bool = False


class Compare(_Strict):
    kind: Literal["period_over_period"] = "period_over_period"
    date_column: str
    start: date
    end: date


class QuerySpec(_Strict):
    select: list[str] = Field(default_factory=list)
    where: list[Filter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    agg: list[Agg] = Field(default_factory=list)
    sort: list[Sort] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)
    compare: Compare | None = None

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True)
class Ambiguity:
    term: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class Validation:
    spec: QuerySpec | None
    errors: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    ambiguous: Ambiguity | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


def parse(raw: Mapping[str, Any]) -> tuple[QuerySpec | None, tuple[str, ...]]:
    try:
        return QuerySpec.model_validate(dict(raw)), ()
    except ValidationError as e:
        errors = tuple(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        return None, errors


def validate(raw: Mapping[str, Any], names: Sequence[str],
             picks: Mapping[str, str] | None = None) -> Validation:
    """Shape first, then columns. `picks` maps an ambiguous term to the header the user chose."""
    parsed, errors = parse(raw)
    if parsed is None:
        return Validation(None, errors, raw=raw)
    chosen = dict(picks or {})
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for term in _terms(parsed):
        if term in chosen and chosen[term] in names:
            resolved[term] = chosen[term]
            continue
        candidates = candidates_for(term, names)
        if len(candidates) == 1:
            resolved[term] = candidates[0]
        elif not candidates:
            missing.append(term)
        else:
            return Validation(None, (), (), Ambiguity(term, candidates[:MAX_CANDIDATES]), raw)
    return Validation(_rewrite(parsed, resolved), (), tuple(dict.fromkeys(missing)), None, raw)


def candidates_for(term: str, names: Sequence[str]) -> tuple[str, ...]:
    """Headers the term may mean: an exact match alone; else every case-insensitive or word-prefix match."""
    if term in names and not _prefixed_by(term, names, exclude=term):
        return (term,)
    key = _norm(term)
    if not key:
        return ()
    return tuple(n for n in names if _norm(n) == key or _norm(n).startswith(key + " "))


def _prefixed_by(term: str, names: Sequence[str], exclude: str) -> bool:
    key = _norm(term)
    return any(n != exclude and _norm(n).startswith(key + " ") for n in names)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", str(text).casefold()).split())


def _terms(spec: QuerySpec) -> list[str]:
    terms = [*spec.select, *(f.column for f in spec.where), *spec.group_by, *(a.column for a in spec.agg)]
    if spec.compare is not None:
        terms.append(spec.compare.date_column)
    return list(dict.fromkeys(terms))


def _rewrite(spec: QuerySpec, resolved: Mapping[str, str]) -> QuerySpec:
    """Real header names in place of the model's terms; parts naming a missing column are left out."""
    def ok(term: str) -> bool:
        return term in resolved

    compare = spec.compare
    if compare is not None:
        compare = compare.model_copy(update={"date_column": resolved[compare.date_column]}) \
            if ok(compare.date_column) else None
    agg = [a.model_copy(update={"column": resolved[a.column]}) for a in spec.agg if ok(a.column)]
    # A sort may name an output label ("signups (sum)"); the engine matches it and skips one it cannot.
    sort = [s.model_copy(update={"column": resolved.get(s.column, s.column)}) for s in spec.sort]
    return QuerySpec(
        select=[resolved[c] for c in spec.select if ok(c)],
        where=[f.model_copy(update={"column": resolved[f.column]}) for f in spec.where if ok(f.column)],
        group_by=[resolved[c] for c in spec.group_by if ok(c)],
        agg=agg, sort=sort, limit=spec.limit, compare=compare,
    )


def agg_label(column: str, fn: str) -> str:
    return f"{column} ({fn})"


def load_answer(text: str) -> Mapping[str, Any] | None:
    """The model's JSON object, tolerating a ```json fence; None when it is not an object."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[4:] if body.casefold().startswith("json") else body
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
