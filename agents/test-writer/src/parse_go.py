"""Pure: Go source -> callables, by regex declarations and brace matching (never compiled or run)."""

from __future__ import annotations

import re

import scan
from code_model import Callable, Param

_FUNC = re.compile(r"^func\s+(?:\(\s*(\w+)?\s*\*?\s*(\w+)(?:\[[^\]]*\])?\s*\)\s*)?(\w+)\s*(?:\[[^\]]*\])?\s*\(",
                   re.MULTILINE)
_PACKAGE = re.compile(r"^package\s+(\w+)", re.MULTILINE)
_IMPORTS = re.compile(r"^import\s+(?:\(\s*(.*?)\)|(\"[^\"]+\"))", re.MULTILINE | re.DOTALL)


def package_of(text: str) -> str:
    match = _PACKAGE.search(text or "")
    return match.group(1) if match else ""


def imports_of(text: str) -> list[str]:
    paths: list[str] = []
    for block, single in _IMPORTS.findall(text or ""):
        paths += re.findall(r"\"([^\"]+)\"", block or single)
    return paths


def parse(text: str, module: str = "") -> tuple[list[Callable], list[str]]:
    found: list[Callable] = []
    for m in _FUNC.finditer(text):
        close = scan.match_close(text, m.end() - 1)
        brace = text.find("{", close) if close != -1 else -1
        if brace == -1:
            continue
        stop = scan.match_close(text, brace)
        source = text[m.start():(stop + 1 if stop != -1 else len(text))]
        found.append(_make(m, text[m.end():close], text[close + 1:brace], source, text, module))
    return found, []


def _make(m: re.Match, params_text: str, returns: str, source: str, text: str, module: str) -> Callable:
    receiver_type, name = m.group(2) or "", m.group(3)
    params = _params(params_text)
    names = [p.name for p in params]
    raises = []
    if re.search(r"errors\.New\(|fmt\.Errorf\(|return [^\n]*\berr\b", source):
        raises.append("error")
    if re.search(r"\bpanic\(", source):
        raises.append("panic")
    public = name[:1].isupper() and (not receiver_type or receiver_type[:1].isupper())
    return Callable(
        name=f"{receiver_type}.{name}" if receiver_type else name, params=tuple(params), owner=receiver_type,
        returns=" ".join(returns.split()), raises=tuple(raises), branches=scan.branches(source),
        comparisons=tuple(c for c in scan.comparisons(source) if not c.value.startswith("'")),
        externals=scan.externals(source, "go"), mutates=scan.mutated(source, names, "go"),
        keys_read=scan.keys_read(source, names), orders=scan.orders(source),
        line=text.count("\n", 0, m.start()) + 1, source=source, module=module, public=public,
    )


def _params(text: str) -> list[Param]:
    """`a, b int, c string` -> a:int, b:int, c:string (a type applies to the bare names before it)."""
    raw = scan.split_top(text)
    params: list[Param] = []
    pending: list[str] = []
    for item in raw:
        bits = item.split(None, 1)
        if len(bits) == 1:
            pending.append(bits[0])
            continue
        name, hint = bits[0], bits[1].strip()
        params += [Param(p, hint) for p in pending] + [Param(name, hint)]
        pending = []
    params += [Param("", p) for p in pending]  # unnamed parameters: types only
    return params
