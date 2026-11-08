"""Pure: a Python file's declarations from `ast`, with exact line spans."""

from __future__ import annotations

import ast

from skeleton_types import Symbol

MAX_DEPTH = 2


def build(text: str) -> tuple[Symbol, ...] | None:
    """None when the file does not parse (the caller falls back to the indentation scanner)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    lines = text.split("\n")
    return tuple(_walk(tree.body, "", 0, lines))


def _walk(nodes: list[ast.stmt], prefix: str, depth: int, lines: list[str]) -> list[Symbol]:
    found: list[Symbol] = []
    for node in nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        name = f"{prefix}{node.name}"
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        kind = "class" if isinstance(node, ast.ClassDef) else ("method" if prefix else "function")
        found.append(Symbol(name, kind, start, node.end_lineno or node.lineno, _signature(lines, node.lineno)))
        if depth + 1 < MAX_DEPTH:
            found.extend(_walk(node.body, f"{name}.", depth + 1, lines))
    return found


def _signature(lines: list[str], lineno: int) -> str:
    return lines[lineno - 1].strip()[:160] if 0 < lineno <= len(lines) else ""
