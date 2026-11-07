"""Pure: from extracted source files to the census for this turn: the parsed callables, which of them are
tested (target selection, cap of 12), and the edge-case ids (E1, E2, ...) computed in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import edge_cases
import parse_go
import parse_js
import parse_py
from code_model import Callable
from edge_cases import EdgeCase
from lang import FAMILY, SUPPORTED, SourceFile, module_of

MAX_CALLABLES = 12
MAX_CALLABLE_CHARS = 8_000
PARSERS = {"python": parse_py.parse, "js": parse_js.parse, "go": parse_go.parse}


@dataclass(frozen=True)
class Census:
    lang: str                                  # python | javascript | typescript | go
    callables: tuple[Callable, ...]            # every callable parsed
    selected: tuple[Callable, ...]             # the ones tested this turn
    cases: tuple[EdgeCase, ...]
    remaining: tuple[str, ...] = ()            # selectable but over the cap: offered as the next batch
    notes: tuple[str, ...] = ()
    source_imports: tuple[str, ...] = ()
    package: str = ""
    modules: dict = field(default_factory=dict)  # callable name -> import module

    @property
    def family(self) -> str:
        return FAMILY[self.lang]

    def cases_of(self, name: str) -> list[EdgeCase]:
        return [c for c in self.cases if c.callable == name]

    def selected_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.selected)

    def names(self) -> set[str]:
        out = set()
        for c in self.callables:
            out |= {c.name, c.short, c.import_name}
        return out


def turn_language(files: tuple[SourceFile, ...]) -> str:
    """The first non-generated file's language ("" when there is none)."""
    usable = [f for f in files if not f.generated] or list(files)
    return usable[0].lang if usable else ""


def build(files: tuple[SourceFile, ...], targets: tuple[str, ...] = (), skip: tuple[str, ...] = (),
          cap: int = MAX_CALLABLES) -> Census:
    lang = turn_language(files)
    family = FAMILY.get(lang, "")
    notes: list[str] = []
    callables: list[Callable] = []
    imports: list[str] = []
    for f in files:
        if f.generated:
            notes.append(f"Skipped {f.path or 'a pasted block'}: it looks generated.")
            continue
        if FAMILY.get(f.lang) != family:
            notes.append(f"Skipped {f.path or 'a pasted block'}: it is {f.lang}, and this turn is {lang}.")
            continue
        found, problems = PARSERS[family](f.text, module_of(f.path, f.lang))
        callables += found
        notes += problems
        imports += _imports(f.text, family)
    package = parse_go.package_of("\n".join(f.text for f in files)) if family == "go" else ""
    return _select(lang, callables, targets, skip, notes, tuple(dict.fromkeys(imports)), package, cap)


def _select(lang: str, callables: list[Callable], targets: tuple[str, ...], skip: tuple[str, ...],
            notes: list[str], imports: tuple[str, ...], package: str, cap: int) -> Census:
    family = FAMILY[lang]
    wanted = [c for c in callables if _named(c, targets)] if targets else [c for c in callables if c.public]
    wanted = [c for c in wanted if c.name not in skip]
    if targets and not wanted:
        notes.append("None of the names you gave match a function or class in the code: " + ", ".join(targets) + ".")
    ok: list[Callable] = []
    for c in wanted:
        if len(c.source) > MAX_CALLABLE_CHARS:
            notes.append(f"Skipped `{c.name}`: it is over {MAX_CALLABLE_CHARS:,} characters; paste it on its own.")
        else:
            ok.append(c)
        if c.unsupported:
            notes.append(f"`{c.name}` uses {c.unsupported}; tests may need adjusting.")
    selected, remaining = ok[:cap], tuple(c.name for c in ok[cap:])
    cases: list[EdgeCase] = []
    for c in selected:
        for kind, label, probe in edge_cases.cases_for(c, family):
            cases.append(EdgeCase(f"E{len(cases) + 1}", c.name, kind, label, probe))
    modules = {c.name: c.module for c in selected}
    return Census(lang, tuple(callables), tuple(selected), tuple(cases), remaining, tuple(notes), imports,
                  package, modules)


def _named(c: Callable, targets: tuple[str, ...]) -> bool:
    keys = {t.casefold() for t in targets}
    return bool({c.name.casefold(), c.short.casefold()} & keys) or (c.public and c.owner.casefold() in keys)


def _imports(text: str, family: str) -> list[str]:
    """Top-level module names the source itself imports (third-party imports allowed in tests)."""
    if family == "python":
        found = re.findall(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text, re.MULTILINE)
        return [(a or b).split(".")[0] for a, b in found]
    if family == "js":
        return re.findall(r"(?:from\s+|require\(\s*)[\"']([^\"']+)[\"']", text)
    return parse_go.imports_of(text)


def supported(lang: str) -> bool:
    return lang in SUPPORTED
