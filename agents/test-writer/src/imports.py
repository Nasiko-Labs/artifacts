"""Pure: which import lines a generated test file may keep. Allowed: the standard library, the test framework,
the module under test (only names that exist in the code) and what the source itself imports."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

FRAMEWORK_MODULES = {"python": {"pytest", "unittest"}, "js": {"jest", "vitest", "@jest/globals"}, "go": {"testing"}}
NODE_BUILTINS = {"fs", "path", "http", "https", "url", "crypto", "events", "util", "os", "child_process", "assert",
                 "stream", "buffer", "timers", "fs/promises"}
_PY = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+\(?([\w\s,*]+)\)?|import\s+([\w.]+)(?:\s+as\s+(\w+))?)\s*$")
_JS = re.compile(r"""^\s*(?:import\s+(?:(.+?)\s+from\s+)?["']([^"']+)["']|(?:const|let|var)\s+(.+?)\s*=\s*"""
                 r"""require\(\s*["']([^"']+)["']\s*\))\s*;?\s*$""")
_GO = re.compile(r"""^\s*(?:import\s+)?(?:\w+\s+)?"([^"]+)"\s*$""")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""
    bound: tuple[str, ...] = ()   # names the import binds (to find the tests that use a removed import)


def is_import(line: str, family: str) -> bool:
    stripped = line.strip()
    if family == "python":
        return bool(_PY.match(stripped))
    if family == "js":
        return bool(_JS.match(stripped))
    return stripped.startswith("import ") or bool(re.fullmatch(r'(?:\w+\s+)?"[^"]+"', stripped))


def judge(line: str, family: str, under_test: set[str], names: set[str], source_imports: set[str]) -> Verdict:
    """`under_test` = module paths of the code under test; `names` = every name defined in it."""
    stripped = line.strip()
    if family == "python":
        m = _PY.match(stripped)
        if not m:
            return Verdict(False, f"not an import line: {stripped[:80]}")
        module = m.group(1) or m.group(3)
        bound = tuple(n.strip().split(" as ")[-1] for n in (m.group(2) or "").split(",") if n.strip()) or \
            (m.group(4) or module.split(".")[0],)
        if module in under_test:
            unknown = [n.strip().split(" as ")[0] for n in (m.group(2) or "").split(",")
                       if n.strip() and n.strip().split(" as ")[0] not in names and n.strip() != "*"]
            return Verdict(not unknown, f"imports unknown name(s): {', '.join(unknown)}" if unknown else "", bound)
        top = module.split(".")[0]
        ok = top in sys.stdlib_module_names or top in FRAMEWORK_MODULES["python"] or top in source_imports
        return Verdict(ok, "" if ok else f"third-party import not used by the source: {module}", bound)
    if family == "js":
        m = _JS.match(stripped)
        if not m:
            return Verdict(False, f"not an import line: {stripped[:80]}")
        what, module = (m.group(1) or m.group(3) or ""), (m.group(2) or m.group(4))
        bound = tuple(re.findall(r"\w+", re.sub(r"\bas\b|\bfrom\b|\btype\b", " ", what)))
        if module in under_test:
            unknown = [n for n in re.findall(r"\{([^}]*)\}", what)[0].replace(" ", "").split(",")
                       if n and n.split("as")[0] not in names] if "{" in what else []
            return Verdict(not unknown, f"imports unknown name(s): {', '.join(unknown)}" if unknown else "", bound)
        ok = (module in FRAMEWORK_MODULES["js"] or module.removeprefix("node:") in NODE_BUILTINS
              or module in source_imports)
        return Verdict(ok, "" if ok else f"import not used by the source: {module}", bound)
    m = _GO.match(stripped)
    if not m:
        return Verdict(False, f"not an import line: {stripped[:80]}")
    path = m.group(1)
    ok = "." not in path.split("/")[0] or path in source_imports
    return Verdict(ok, "" if ok else f"import not used by the source: {path}", (path.split("/")[-1],))
