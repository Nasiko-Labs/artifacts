"""Pure: the final test file. Imports hoisted and deduped (the module-under-test import added by code), tests
ordered by callable, reasoned tests marked with a comment, caps applied, and the Python file compiled once
more (never run)."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

import syntax
from census import Census
from checks import REASONED_NOTE, GenTest

MAX_TESTS = 40
MAX_LINES = 400
PLACEHOLDER = {"python": "module_under_test", "js": "./module_under_test"}
CHANGE_IMPORT = "change the import to your module path"


@dataclass(frozen=True)
class Assembled:
    code: str
    tests: tuple[GenTest, ...]
    overflow: tuple[str, ...] = ()
    placeholder_import: bool = False
    file_ok: bool = True


def _comment(family: str) -> str:
    return "#" if family == "python" else "//"


def _body(test: GenTest, family: str) -> str:
    code = textwrap.dedent(test.code).strip("\n")
    if test.basis == "reasoned" and REASONED_NOTE not in code:
        code = f"{_comment(family)} {REASONED_NOTE}\n{code}"
    return code


def _module_lines(census: Census, tests: list[GenTest], override: str) -> tuple[list[str], bool]:
    family = census.family
    if family == "go":
        return [], False
    by_module: dict[str, list[str]] = {}
    by_name = {c.name: c for c in census.selected}
    for test in tests:
        c = by_name.get(test.callable)
        if c is None:
            continue
        module = override or census.modules.get(c.name) or PLACEHOLDER[family]
        by_module.setdefault(module, [])
        if c.import_name not in by_module[module]:
            by_module[module].append(c.import_name)
    lines, placeholder = [], False
    for module, names in by_module.items():
        tail = ""
        if module == PLACEHOLDER[family]:
            placeholder, tail = True, f"  {_comment(family)} {CHANGE_IMPORT}"
        if family == "python":
            lines.append(f"from {module} import {', '.join(names)}{tail}")
        else:
            lines.append(f'import {{ {", ".join(names)} }} from "{module}";{tail}')
    return lines, placeholder


def _header(census: Census, framework: str, imports: list[str], module_lines: list[str]) -> str:
    family = census.family
    if family == "go":
        paths = ["testing"] + [p for line in imports for p in re.findall(r'"([^"]+)"', line)]
        quoted = "\n".join(f'\t"{p}"' for p in dict.fromkeys(paths))
        package = census.package or "yourpkg  // change to your package name"
        return f"package {package}\n\nimport (\n{quoted}\n)"
    first = {"pytest": ["import pytest"], "unittest": ["import unittest"],
             "vitest": ['import { describe, it, expect, vi } from "vitest";']}.get(framework, [])
    lines = list(dict.fromkeys([*first, *(i.strip() for i in imports)]))
    return "\n".join(lines + module_lines)


def _group(census: Census, tests: list[GenTest], framework: str) -> str:
    family = census.family
    if family == "go" or framework == "pytest":
        return "\n\n\n".join(_body(t, family) for t in tests) if family == "python" else \
            "\n\n".join(_body(t, family) for t in tests)
    blocks = []
    for c in census.selected:
        mine = [t for t in tests if t.callable == c.name]
        if not mine:
            continue
        if framework == "unittest":
            inner = "\n\n".join(textwrap.indent(_body(t, family), "    ") for t in mine)
            blocks.append(f"class Test{re.sub(r'[^0-9A-Za-z]', '', c.name.title())}(unittest.TestCase):\n{inner}")
        else:
            inner = "\n\n".join(textwrap.indent(_body(t, family), "  ") for t in mine)
            blocks.append(f'describe("{c.name}", () => {{\n{inner}\n}});')
    tail = '\n\n\nif __name__ == "__main__":\n    unittest.main()' if framework == "unittest" else ""
    return ("\n\n\n" if family == "python" else "\n\n").join(blocks) + tail


def assemble(census: Census, tests: list[GenTest], helpers: list[str], imports: list[str], framework: str,
             module_override: str = "") -> Assembled:
    order = {c.name: i for i, c in enumerate(census.selected)}
    ordered = sorted(tests, key=lambda t: order.get(t.callable, len(order)))
    all_imports = [*imports, *(i for t in ordered for i in t.imports)]
    shown: list[GenTest] = []
    lines = 12 + sum(len(h.splitlines()) for h in helpers)
    for test in ordered:
        size = len(_body(test, census.family).splitlines()) + 2
        if len(shown) >= MAX_TESTS or lines + size > MAX_LINES:
            break
        shown.append(test)
        lines += size
    overflow = tuple(t.name for t in ordered[len(shown):])
    module_lines, placeholder = _module_lines(census, shown, module_override)
    parts = [_header(census, framework, all_imports, module_lines),
             "\n\n".join(textwrap.dedent(h).strip("\n") for h in dict.fromkeys(helpers)),
             _group(census, shown, framework)]
    code = "\n\n\n".join(p for p in parts if p.strip()) + "\n"
    ok = not syntax.python_ok(code) if census.family == "python" else not syntax.balanced(code)
    return Assembled(code, tuple(shown), overflow, placeholder, ok)
