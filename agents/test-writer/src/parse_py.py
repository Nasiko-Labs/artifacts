"""Pure: Python source -> callables, with the stdlib `ast` (never executed)."""

from __future__ import annotations

import ast

import scan
from code_model import Callable, Comparison, Param

SAFE_DECORATORS = {"staticmethod", "classmethod", "property", "wraps", "cache", "lru_cache", "cached_property",
                   "abstractmethod", "override", "setter", "getter", "deleter"}
_OPS = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.Eq: "==", ast.NotEq: "!="}
_BRANCH_NODES = (ast.If, ast.IfExp, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler, ast.BoolOp, ast.match_case)


def parse(text: str, module: str = "") -> tuple[list[Callable], list[str]]:
    """(callables, problems). Top-level functions, and the methods of top-level classes."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [], [f"I couldn't parse {module or 'the Python code'} (syntax error at line {e.lineno})."]
    found: list[Callable] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(_callable(node, text, module, ""))
        elif isinstance(node, ast.ClassDef):
            meta = any(k.arg == "metaclass" for k in node.keywords)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append(_callable(item, text, module, node.name, meta, _private(node.name)))
    return found, []


def _private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _callable(node, text: str, module: str, owner: str, meta: bool = False, owner_private: bool = False) -> Callable:
    source = ast.get_source_segment(text, node) or ""
    params = _params(node.args, bool(owner) and not _is_static(node))
    names = [p.name.lstrip("*") for p in params]
    public = not owner_private and (not _private(node.name) or node.name == "__init__")
    if owner and node.name.startswith("__") and node.name != "__init__":
        public = False
    return Callable(
        name=f"{owner}.{node.name}" if owner else node.name, params=tuple(params), owner=owner,
        returns=ast.unparse(node.returns) if node.returns else "", raises=_raises(node),
        doctests=_doctests(ast.get_docstring(node) or ""), branches=_branches(node),
        comparisons=_comparisons(node), externals=scan.externals(source, "python"),
        mutates=scan.mutated(source, names, "python"), keys_read=scan.keys_read(source, names),
        orders=scan.orders(source), line=node.lineno, source=source, module=module, public=public,
        unsupported=_unsupported(node, meta),
    )


def _is_static(node) -> bool:
    return any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list)


def _params(args: ast.arguments, drop_first: bool) -> list[Param]:
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    params = [_param(a, d) for a, d in zip(positional, defaults, strict=True)]
    if drop_first and params:
        params = params[1:]
    if args.vararg:
        params.append(_param(args.vararg, None, "*"))
    params += [_param(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)]
    if args.kwarg:
        params.append(_param(args.kwarg, None, "**"))
    return params


def _param(arg: ast.arg, default, prefix: str = "") -> Param:
    hint = ast.unparse(arg.annotation) if arg.annotation else ""
    return Param(prefix + arg.arg, hint, ast.unparse(default) if default is not None else None)


def _raises(node) -> tuple[str, ...]:
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Raise) and sub.exc is not None:
            target = sub.exc.func if isinstance(sub.exc, ast.Call) else sub.exc
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                names.append(target.attr)
        elif isinstance(sub, ast.Assert):
            names.append("AssertionError")
    return tuple(dict.fromkeys(names))


def _branches(node) -> int:
    count = 0
    for sub in ast.walk(node):
        if isinstance(sub, _BRANCH_NODES):
            count += 1
        elif isinstance(sub, ast.comprehension):
            count += len(sub.ifs)
    return count


def _literal(node) -> tuple[str, str] | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        inner = _literal(node.operand)
        return ("-" + inner[0], "number") if inner and inner[1] == "number" else None
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return None
    if isinstance(node.value, (int, float)):
        return repr(node.value), "number"
    if isinstance(node.value, str):
        return repr(node.value), "string"
    return None


def _comparisons(node) -> tuple[Comparison, ...]:
    found: list[Comparison] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare):
            left = sub.left
            for op, right in zip(sub.ops, sub.comparators, strict=True):
                symbol = _OPS.get(type(op))
                lit_r, lit_l = _literal(right), _literal(left)
                if symbol and lit_r and not lit_l:
                    found.append(Comparison(ast.unparse(left), symbol, *lit_r))
                elif symbol and lit_l and not lit_r:
                    found.append(Comparison(ast.unparse(right), symbol, *lit_l))
                left = right
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.args and \
                sub.func.attr in ("startswith", "endswith"):
            lit = _literal(sub.args[0])
            if lit and lit[1] == "string":
                found.append(Comparison(ast.unparse(sub.func.value), sub.func.attr, *lit))
        elif isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mod):
            lit = _literal(sub.right)
            if lit and lit[1] == "number":
                found.append(Comparison(ast.unparse(sub.left), "%", *lit))
    return tuple(dict.fromkeys(found))


def _doctests(doc: str) -> tuple[tuple[str, str], ...]:
    examples: list[tuple[str, str]] = []
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(">>> "):
            continue
        expected = []
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.strip().startswith(">>>"):
                break
            expected.append(nxt.strip())
        if expected:
            examples.append((stripped[4:].strip(), "\n".join(expected)))
    return tuple(examples)


def _unsupported(node, meta: bool) -> str:
    if meta:
        return "a metaclass"
    if isinstance(node, ast.AsyncFunctionDef) and any(isinstance(s, (ast.Yield, ast.YieldFrom)) for s in ast.walk(node)):
        return "an async generator"
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name not in SAFE_DECORATORS:
            return f"the decorator @{name or ast.unparse(dec)}"
    return ""
