"""Pure: the code checks on every test the model returns. A test is kept, sent to the one repair call
(syntax, real network), or dropped with a reason; edge-case claims that code cannot verify are removed."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import imports as imports_mod
import syntax
from census import Census

BASES = ("doctest", "code", "user", "reasoned")
REASONED_NOTE = "expected value reasoned from the code, not executed — verify"
_ASSERT = {"python": re.compile(r"\bassert\b|self\.assert\w+\(|pytest\.raises|\.assert_\w+\("),
           "js": re.compile(r"\bexpect\("),
           "go": re.compile(r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\b|\bassert\.\w+\(|\brequire\.\w+\(")}
_TRIVIAL = re.compile(r"\bassert\s+(?:True|1|not\s+False)\s*$|expect\(\s*true\s*\)\.toBe\(\s*true\s*\)",
                      re.MULTILINE)
_SWALLOW = re.compile(r"except(?:\s+\w+)?\s*:\s*\n?\s*pass\b|catch\s*(?:\(\w*\))?\s*\{\s*\}")
_SLEEP = re.compile(r"\btime\.sleep\(|\bsetTimeout\(|\btime\.Sleep\(|\bawait\s+sleep\(")
_NETWORK = re.compile(r"\brequests\.(?:get|post|put|patch|delete)\(|\bhttpx\.(?:get|post)\(|\burlopen\(|"
                      r"(?<![\w.])fetch\(|\baxios\.\w+\(|\bhttp\.(?:Get|Post)\(")
MOCKED = re.compile(r"monkeypatch|patch\(|\bpatch\.|Mock\(|mock\.|jest\.(?:mock|spyOn|fn)|vi\.(?:mock|spyOn|fn)|"
                     r"httptest|respx|responses\.|global\.fetch\s*=")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_NAMES = {"python": re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", re.MULTILINE),
          "js": re.compile(r"\b(?:it|test)(?:\.each\s*\([\s\S]*?\)\s*)?\(\s*([\"'`])(.+?)\1"),
          "go": re.compile(r"^\s*func\s+(Test\w+)\s*\(", re.MULTILINE)}


@dataclass(frozen=True)
class GenTest:
    callable: str
    name: str
    code: str
    covers: tuple[str, ...] = ()
    basis: str = "reasoned"
    uses_mocks: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"callable": self.callable, "name": self.name, "covers": list(self.covers), "basis": self.basis,
                "code": self.code, "uses_mocks": list(self.uses_mocks), "imports": list(self.imports)}

    @staticmethod
    def from_dict(data: dict) -> GenTest:
        return GenTest(str(data["callable"]), str(data["name"]), str(data["code"]), tuple(data.get("covers") or ()),
                       str(data.get("basis") or "reasoned"), tuple(data.get("uses_mocks") or ()),
                       tuple(data.get("imports") or ()))


@dataclass
class Ledger:
    """What happened to the batch's tests (mutable while one batch is checked)."""

    kept: list[GenTest] = field(default_factory=list)
    repair: list[tuple[dict, list[str]]] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    claims_removed: int = 0
    relabelled: int = 0


def allowed_numbers(*texts: str) -> set[float]:
    """Every number written in the source, the user's text or the census (as values: 100 == 100.0)."""
    found = {0.0, 1.0, 2.0, -1.0}
    for text in texts:
        found |= {float(n) for n in _NUMBER.findall(text or "")}
    return found


def _name_of(code: str, family: str) -> str:
    match = _NAMES[family].search(code)
    if not match:
        return ""
    return match.group(2) if family == "js" else match.group(1)


def _hoist(code: str, family: str) -> tuple[str, tuple[str, ...]]:
    """Column-0 import lines move out of the test body into its import list."""
    kept, hoisted = [], []
    for line in code.splitlines():
        if line[:1].strip() and imports_mod.is_import(line, family):
            hoisted.append(line.strip())
        else:
            kept.append(line)
    return "\n".join(kept).strip("\n"), tuple(hoisted)


def problems(code: str, family: str) -> list[str]:
    """Repairable violations: syntax and an unmocked real network call."""
    found = []
    error = syntax.check(code, family)
    if error:
        found.append(error)
    if _NETWORK.search(code) and not MOCKED.search(code):
        found.append("calls the real network; patch the external instead")
    return found


def fatal(code: str, family: str) -> str:
    if not _ASSERT[family].search(code):
        return "no assertion"
    if _TRIVIAL.search(code):
        return "trivial assertion"
    if _SWALLOW.search(code):
        return "swallows exceptions"
    if _SLEEP.search(code):
        return "sleeps"
    return ""


@dataclass(frozen=True)
class Context:
    census: Census
    framework: str
    batch: tuple[str, ...]                 # callable names in this batch
    numbers: frozenset[float]
    existing: frozenset[str] = frozenset()


def check_tests(raw_tests: Iterable, ctx: Context, seen: set[str], ledger: Ledger, repairable: bool = True) -> None:
    for raw in raw_tests:
        _check_one(raw, ctx, seen, ledger, repairable)


def _check_one(raw, ctx: Context, seen: set[str], ledger: Ledger, repairable: bool) -> None:
    family = ctx.census.family
    if not isinstance(raw, dict) or not isinstance(raw.get("code"), str) or not raw["code"].strip():
        ledger.dropped.append((str(raw.get("name") if isinstance(raw, dict) else "?"), "malformed"))
        return
    target = next((c for c in ctx.census.selected if c.name in ctx.batch and
                   str(raw.get("callable") or "") in (c.name, c.short)), None)
    code, hoisted = _hoist(raw["code"], family)
    name = _name_of(code, family) or str(raw.get("name") or "")
    if target is None:
        ledger.dropped.append((name, "not one of the callables to test"))
        return
    if not name or (family != "js" and not _name_of(code, family)):
        ledger.dropped.append((name or "?", "no test function found"))
        return
    if name in ctx.existing:
        ledger.dropped.append((name, "already in your existing tests"))
        return
    found = problems(code, family)
    if found:
        if repairable:
            ledger.repair.append((raw, found))
        else:
            ledger.dropped.append((name, "; ".join(found)))
        return
    reason = fatal(code, family) or ("does not call " + target.short if not re.search(
        rf"\b{re.escape(target.short)}\b", code) else "")
    if reason:
        ledger.dropped.append((name, reason))
        return
    if name in seen:
        ledger.dropped.append((name, "duplicate name"))
        return
    seen.add(name)
    ledger.kept.append(_claims(GenTest(target.name, name, code, (), _basis(raw), _strs(raw.get("uses_mocks")),
                                       tuple(dict.fromkeys(hoisted))), raw, ctx, ledger))


def _strs(value) -> tuple[str, ...]:
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def _basis(raw: dict) -> str:
    basis = str(raw.get("basis") or "").casefold()
    return basis if basis in BASES else "reasoned"


def _claims(test: GenTest, raw: dict, ctx: Context, ledger: Ledger) -> GenTest:
    own = {c.id: c for c in ctx.census.cases_of(test.callable)}
    covers = []
    for case_id in _strs(raw.get("covers")):
        case = own.get(case_id)
        if case is None or (case.probe and not re.search(case.probe, test.code)):
            ledger.claims_removed += 1
            continue
        if case_id not in covers:
            covers.append(case_id)
    basis = test.basis
    if basis != "reasoned" and any(float(n) not in ctx.numbers for n in _NUMBER.findall(test.code)):
        basis = "reasoned"
        ledger.relabelled += 1
    return replace(test, covers=tuple(covers), basis=basis)
