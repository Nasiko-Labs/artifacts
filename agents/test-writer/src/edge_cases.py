"""Pure: the edge-case catalogue for one callable, computed in code. Each case is a census entry; a test can
claim it only by its id, and a case with a `probe` is verified by finding that literal in the test body.

Order (and the cap of 24 per callable): boundaries of comparison constants, raised errors, doctest examples,
then cases by parameter type, then purity / mutation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from code_model import Callable, Comparison, Param
from param_kinds import DATE, NULLABLE, kind_of

MAX_CASES_PER_CALLABLE = 24
LARGE = "1,000,000,000"
NULL = {"python": "None", "js": "null", "go": "nil"}
NULL_PROBE = r"\bNone\b|\bnull\b|\bundefined\b|\bnil\b"


@dataclass(frozen=True)
class EdgeCase:
    id: str
    callable: str
    kind: str          # boundary | raises | doctest | type | purity | mutation
    label: str
    probe: str = ""    # a regex that must match the test body for a claim to count; "" = no literal check

    def to_dict(self) -> dict:
        return {"id": self.id, "callable": self.callable, "kind": self.kind, "label": self.label, "probe": self.probe}

    @staticmethod
    def from_dict(data: dict) -> EdgeCase:
        return EdgeCase(str(data["id"]), str(data["callable"]), str(data["kind"]), str(data["label"]),
                        str(data.get("probe") or ""))


def number_probe(value: str) -> str:
    """A regex finding the numeric literal `value` as a whole token (`-1` also as `- 1`)."""
    body = value.lstrip("-")
    if "." in body:
        whole, frac = body.split(".", 1)
        body = re.escape(whole) + r"\." + re.escape(frac.rstrip("0") or "0") + "0*"
    else:
        body = re.escape(body)
    sign = r"-\s*" if value.startswith("-") else r"(?<![\w.-])"
    return sign + body + r"(?![\w.])"


def string_probe(literal: str) -> str:
    inner = literal[1:-1] if len(literal) >= 2 and literal[0] in "\"'`" else literal
    return r"([\"'`])" + re.escape(inner) + r"\1"


def _mentions(comparison: Comparison, name: str) -> bool:
    return bool(re.search(rf"\b{re.escape(name)}\b", comparison.subject))


def _boundaries(c: Callable) -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    for comp in c.comparisons:
        if comp.kind != "number":
            continue
        try:
            values = [str(int(comp.value) + d) for d in (-1, 0, 1)] if "." not in comp.value else [comp.value]
        except ValueError:
            continue
        if comp.op == "%":
            values = [comp.value]
        for value in values:
            if comp.subject.startswith("len(") and value.startswith("-"):
                continue
            label = f"{comp.subject} = {value}" if comp.op != "%" else f"{comp.subject} divisible by {value}"
            cases.append(("boundary", label, number_probe(value)))
    return cases


def _raises(c: Callable, family: str) -> list[tuple[str, str, str]]:
    cases = []
    for name in c.raises:
        if family == "go":
            probe = r"\berr\b|Error" if name == "error" else r"recover\(|panic"
            label = "returns an error" if name == "error" else "panics"
        else:
            probe = rf"\b{re.escape(name)}\b" + (r"|toThrow" if family == "js" else "")
            label = f"raises {name}" if family == "python" else f"throws {name}"
        cases.append(("raises", label, probe))
    return cases


def _doctests(c: Callable) -> list[tuple[str, str, str]]:
    return [("doctest", f"doctest {expr} -> {expected}", r"\s*".join(re.escape(t) for t in expected.split()))
            for expr, expected in c.doctests]


def _string_cases(c: Callable, name: str) -> list[tuple[str, str, str]]:
    cases = [("type", f"{name} empty string", r"([\"'`])\1"),
             ("type", f"{name} whitespace only", r"([\"'`])(?:[ \t]|\\t|\\n)+\1"),
             ("type", f"{name} one character", r"([\"'`])[^\"'`\\\s]\1"),
             ("type", f"{name} non-ASCII", r"[^\x00-\x7f]|\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}"),
             ("type", f"{name} very long (1,000 chars)", r"\*\s*1_?000\b|repeat\(\s*1_?000|Repeat\([^)]*1_?000")]
    for comp in c.comparisons:
        if comp.kind == "string" and _mentions(comp, name):
            word = {"startswith": "starting with", "endswith": "ending with"}.get(comp.op, "equal to")
            cases.append(("boundary", f"{name} {word} {comp.value}", string_probe(comp.value)))
    return cases


def _type_cases(c: Callable, param: Param, family: str) -> list[tuple[str, str, str]]:
    name, kind = param.name.lstrip("*."), kind_of(param, family)
    if not name:
        return []
    cases: list[tuple[str, str, str]] = []
    if kind == "number":
        cases = [("type", f"{name} = {v}", number_probe(v)) for v in ("0", "-1", "1")]
        cases.append(("type", f"{name} very large ({LARGE})", r"10\s*\*\*\s*9|1e9|1_?000_?000_?000"))
    elif kind == "float":
        cases = [("type", f"{name} = 0.0", r"(?<![\w.])0\.0\b"), ("type", f"{name} negative", r"-\s*\d"),
                 ("type", f"{name} tiny positive", r"\de-\d|0\.0+1")]
        if any(_mentions(comp, name) for comp in c.comparisons):
            cases += [("type", f"{name} NaN", r"nan|NaN"), ("type", f"{name} infinity", r"inf|Infinity|Inf\(")]
    elif kind == "string":
        cases = _string_cases(c, name)
    elif kind == "list":
        cases = [("type", f"{name} empty", r"\[\s*\]|\{\s*\}|\(\s*\)"), ("type", f"{name} one item", ""),
                 ("type", f"{name} with duplicates", "")]
        if c.orders:
            cases.append(("type", f"{name} already sorted / reversed", ""))
        if family != "go":
            cases.append(("type", f"{name} containing {NULL[family]}", NULL_PROBE))
    elif kind == "dict":
        cases = [("type", f"{name} empty", r"\{\s*\}|dict\(\)")]
        cases += [("type", f"{name} missing key '{k}'", "") for k in c.keys_read]
        cases.append(("type", f"{name} with extra keys", ""))
    elif kind == "bool":
        cases = [("type", f"{name} = {v}", rf"\b{v}\b") for v in (("True", "False") if family == "python"
                                                                   else ("true", "false"))]
    elif kind == "date" and DATE.search(c.source):
        cases = [("type", f"{name} at midnight", r"0,\s*0\b|00:00|T00"), ("type", f"{name} at month end", ""),
                 ("type", f"{name} on a leap day", r"2,\s*29|-02-29"),
                 ("type", f"{name} timezone-naive vs aware", r"tzinfo|timezone|UTC|tz=")]
    if NULLABLE.search(param.type_hint) or param.default in ("None", "null", "undefined"):
        cases.append(("type", f"{name} = {NULL[family]}", NULL_PROBE))
    return cases


def cases_for(c: Callable, family: str) -> list[tuple[str, str, str]]:
    """(kind, label, probe) for one callable, ordered and capped; ids are given by the census."""
    cases = _boundaries(c) + _raises(c, family) + _doctests(c)
    for param in c.params:
        cases += _type_cases(c, param, family)
    if not c.externals and not c.mutates and not c.owner:
        cases.append(("purity", "same result when called twice with the same arguments", ""))
    cases += [("mutation", f"mutates its argument {p}", "") for p in c.mutates]
    seen: set[str] = set()
    unique = []
    for case in cases:
        if case[1] not in seen:
            seen.add(case[1])
            unique.append(case)
    return unique[:MAX_CASES_PER_CALLABLE]
