"""Pure: attribute-by-attribute comparison of an expected object (the IaC, or the last known state) with
the actual one (live). Deterministic; the model never decides whether two values differ.

Only keys the expected side sets are compared. Keys only the live side has are `extra_live` (kept apart,
shown only when asked; bookkeeping keys on the ignore list are never extras). An expected value that is
an expression this agent does not evaluate (`Unresolved`) is skipped and counted, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hcl_lite import Unresolved, has_unresolved
from normalise import (
    canon_key,
    is_ignored,
    is_scalar,
    is_set_like,
    item_key,
    lookup,
    same_scalar,
    same_set,
    scalar,
)

Path = tuple[str | int, ...]


@dataclass(frozen=True)
class Diff:
    path: Path
    kind: str  # changed | missing_live | extra_live
    expected: Any
    actual: Any


@dataclass(frozen=True)
class Comparison:
    diffs: tuple[Diff, ...] = ()
    extras: tuple[Diff, ...] = ()
    compared: int = 0
    unresolved: int = 0

    def __add__(self, other: Comparison) -> Comparison:
        return Comparison(self.diffs + other.diffs, self.extras + other.extras, self.compared + other.compared,
                          self.unresolved + other.unresolved)


EMPTY = Comparison()


def compare(expected: Any, actual: Any, path: Path = ()) -> Comparison:
    return _walk(expected, actual, path)


def _walk(exp: Any, act: Any, path: Path) -> Comparison:
    if isinstance(exp, Unresolved) or isinstance(act, Unresolved):
        return Comparison(unresolved=1)
    if isinstance(exp, list) and len(exp) == 1 and isinstance(exp[0], dict) and isinstance(act, dict):
        return _walk(exp[0], act, path)  # one nested HCL block vs one live object
    if isinstance(exp, dict) and isinstance(act, dict):
        return _dict(exp, act, path)
    if isinstance(exp, list) and isinstance(act, list):
        return _list(exp, act, path)
    if is_scalar(exp) and is_scalar(act):
        same = same_scalar(exp, act) or (_blank(exp) and _blank(act))
        return Comparison(compared=1) if same else Comparison((Diff(path, "changed", exp, act),), compared=1)
    if _blank(exp) and _blank(act):
        return Comparison(compared=1)
    return Comparison((Diff(path, "changed", exp, act),), compared=1)


def _dict(exp: dict, act: dict, path: Path) -> Comparison:
    out = EMPTY
    matched: set = set()
    for key, value in exp.items():
        found, live_value = lookup(act, key)
        if found:
            matched.add(_live_key(act, key))
            out = out + _walk(value, live_value, (*path, key))
        elif has_unresolved(value):
            out = out + Comparison(unresolved=1)
        elif not _blank(value):
            out = out + Comparison((Diff((*path, key), "missing_live", value, None),), compared=1)
    depth = sum(1 for p in path if isinstance(p, str))
    extras = tuple(Diff((*path, k), "extra_live", None, v) for k, v in act.items()
                   if k not in matched and not _blank(v) and not is_ignored(k, depth))
    return out + Comparison(extras=extras)


def _live_key(act: dict, key: Any) -> Any:
    if key in act:
        return key
    return next(k for k in act if canon_key(k) == canon_key(key))


def _list(exp: list, act: list, path: Path) -> Comparison:
    if any(isinstance(v, Unresolved) for v in exp):
        return Comparison(unresolved=1)
    if all(is_scalar(v) for v in exp) and all(is_scalar(v) for v in act):
        last = next((p for p in reversed(path) if isinstance(p, str)), "")
        same = same_set(exp, act) if is_set_like(last) else (
            len(exp) == len(act) and all(same_scalar(a, b) for a, b in zip(exp, act, strict=True)))
        return Comparison(compared=1) if same else Comparison((Diff(path, "changed", exp, act),), compared=1)
    key = item_key(exp)
    if key and item_key(act) == key:
        return _keyed(exp, act, path, key)
    if len(exp) == len(act):
        out = EMPTY
        for i, (a, b) in enumerate(zip(exp, act, strict=True)):
            out = out + _walk(a, b, (*path, i))
        return out
    return Comparison((Diff(path, "changed", exp, act),), compared=1)


def _keyed(exp: list, act: list, path: Path, key: str) -> Comparison:
    def ident(item: dict) -> Any:
        found, value = lookup(item, key)
        return repr(scalar(value)) if found else None

    live = {ident(item): item for item in act}
    out = EMPTY
    for i, item in enumerate(exp):
        other = live.pop(ident(item), None)
        if other is None:
            out = out + Comparison((Diff((*path, i), "missing_live", item, None),), compared=1)
        else:
            out = out + _walk(item, other, (*path, i))
    extras = tuple(Diff((*path, len(exp) + n), "extra_live", None, item) for n, item in enumerate(live.values()))
    return out + Comparison(extras=extras)


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}
