"""Pure: JavaScript/TypeScript source -> callables, by regex declarations and brace matching (never run).
Declarations with unusual syntax may be missed; the reply says which callables were found."""

from __future__ import annotations

import re

import scan
from code_model import Callable, Param

_FUNCTION = re.compile(r"^[ \t]*(export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*(\w+)\s*(?:<[^>(]*>)?\s*\(",
                       re.MULTILINE)
_ARROW = re.compile(r"^[ \t]*(export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*(?:async\s+)?"
                    r"(?:function\s*\w*\s*)?(?:<[^>(]*>)?\s*(\(|\w+\s*=>)", re.MULTILINE)
_CLASS = re.compile(r"^[ \t]*(export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(\w+)[^{]*\{", re.MULTILINE)
_METHOD = re.compile(r"^[ \t]*((?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*)"
                     r"(#?\w+)\s*(?:<[^>(]*>)?\s*\(", re.MULTILINE)
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "constructor_", "super", "new", "await"}
_EXPORT_LIST = re.compile(r"(?:module\.exports\s*=|export)\s*\{([^}]*)\}")
_EXPORT_ONE = re.compile(r"(?:module\.)?exports\.(\w+)\s*=")
_BODY_LEAD = re.compile(r"\s*(?::\s*[^={;\n]+)?\s*(?:=>\s*)?")
_THROW = re.compile(r"throw\s+new\s+(\w+)|throw\s+(\w+)\b")


def parse(text: str, module: str = "") -> tuple[list[Callable], list[str]]:
    exported = _exported(text)
    has_exports = bool(exported) or bool(re.search(r"^\s*export\s", text, re.MULTILINE))
    found: list[Callable] = []
    class_spans = []
    for m in _CLASS.finditer(text):
        end = scan.match_close(text, m.end() - 1)
        class_spans.append((m.start(), end))
        public = bool(m.group(1)) or m.group(2) in exported or not has_exports
        found += _methods(text, m.group(2), m.end(), end, module, public)
    for rx in (_FUNCTION, _ARROW):
        for m in rx.finditer(text):
            if any(s <= m.start() < e for s, e in class_spans):
                continue
            item = _function(text, m, module, bool(m.group(1)) or m.group(2) in exported or not has_exports)
            if item:
                found.append(item)
    found.sort(key=lambda c: c.line)
    return found, []


def _exported(text: str) -> set[str]:
    names = set(_EXPORT_ONE.findall(text))
    for group in _EXPORT_LIST.findall(text):
        names |= {p.split(":")[0].split(" as ")[0].strip() for p in group.split(",") if p.strip()}
    return names


def _function(text: str, m: re.Match, module: str, public: bool) -> Callable | None:
    name = m.group(2)
    if m.re is _ARROW and not m.group(3).startswith("("):
        params_text, after = m.group(3).split("=>")[0].strip(), m.end()
    else:
        close = scan.match_close(text, m.end() - 1)
        if close == -1:
            return None
        params_text, after = text[m.end():close], close + 1
    body_end = _body_end(text, after)
    source = text[m.start():body_end].strip()
    return _make(name, params_text, source, text, m.start(), module, "", public and not name.startswith("_"))


def _body_end(text: str, after: int) -> int:
    """End of a function body: a braced block, or an arrow's expression up to `;` or the end of the line."""
    lead = _BODY_LEAD.match(text, after)
    pos = lead.end() if lead else after
    if pos < len(text) and text[pos] == "{":
        close = scan.match_close(text, pos)
        return len(text) if close == -1 else close + 1
    stops = [i for i in (text.find(";", pos), text.find("\n", pos)) if i != -1]
    return min(stops) + 1 if stops else len(text)


def _methods(text: str, owner: str, start: int, end: int, module: str, public: bool) -> list[Callable]:
    found: list[Callable] = []
    body = text[start:end]
    depth_at = _depths(body)
    for m in _METHOD.finditer(body):
        name, mods = m.group(2), m.group(1)
        if depth_at[m.start(2)] != 0 or name in _KEYWORDS:
            continue
        close = scan.match_close(body, m.end() - 1)
        brace = body.find("{", close)
        if close == -1 or brace == -1:
            continue
        stop = scan.match_close(body, brace)
        source = body[m.start():stop + 1].strip()
        private = name.startswith(("#", "_")) or "private" in mods
        found.append(_make(f"{owner}.{name}", body[m.end():close], source, text, start + m.start(), module, owner,
                           public and not private))
    return found


def _depths(body: str) -> list[int]:
    depths, depth, i = [0] * (len(body) + 1), 0, 0
    while i < len(body):
        nxt = scan.skip_string(body, i)
        if nxt != i:
            for j in range(i, min(nxt, len(body))):
                depths[j] = depth
            i = nxt
            continue
        depth += {"{": 1, "}": -1}.get(body[i], 0)
        depths[i] = depth
        i += 1
    return depths


def _make(name: str, params_text: str, source: str, text: str, pos: int, module: str, owner: str,
          public: bool) -> Callable:
    params = [_param(p) for p in scan.split_top(params_text)]
    names = [p.name.lstrip(".") for p in params]
    raises = [a or b for a, b in _THROW.findall(source)]
    return Callable(
        name=name, params=tuple(params), owner=owner, raises=tuple(dict.fromkeys(raises)),
        branches=scan.branches(source), comparisons=scan.comparisons(source),
        externals=scan.externals(source, "js"), mutates=scan.mutated(source, names, "js"),
        keys_read=scan.keys_read(source, names), orders=scan.orders(source),
        line=text.count("\n", 0, pos) + 1, source=source, module=module, public=public,
    )


def _param(text: str) -> Param:
    name_part, has_default, default = text.partition("=")
    name, _, hint = name_part.partition(":")
    return Param(name.strip().rstrip("?"), " ".join(hint.split()), default.strip() if has_default else None)
