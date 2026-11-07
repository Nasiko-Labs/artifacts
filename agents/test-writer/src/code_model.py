"""The parsed shape of the code under test, shared by the three parsers, the census and the checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    name: str
    type_hint: str = ""
    default: str | None = None


@dataclass(frozen=True)
class Comparison:
    """`subject op value` with a literal on one side, e.g. `percent > 100` or `s.startswith("http")`."""

    subject: str
    op: str
    value: str          # the literal as written in the source
    kind: str           # number | string


@dataclass(frozen=True)
class Callable:
    name: str                                   # qualified: `apply_discount`, `Cart.add`
    params: tuple[Param, ...] = ()
    owner: str = ""                             # class / receiver type for methods
    returns: str = ""
    raises: tuple[str, ...] = ()
    doctests: tuple[tuple[str, str], ...] = ()  # (expression, expected output)
    branches: int = 0
    comparisons: tuple[Comparison, ...] = ()
    externals: tuple[str, ...] = ()
    mutates: tuple[str, ...] = ()
    keys_read: tuple[str, ...] = ()
    orders: bool = False
    line: int = 0
    source: str = ""
    module: str = ""
    public: bool = True
    unsupported: str = ""

    @property
    def short(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def import_name(self) -> str:
        return self.owner or self.name

    def signature(self) -> str:
        parts = []
        for p in self.params:
            text = p.name + (f": {p.type_hint}" if p.type_hint else "")
            parts.append(text + (f" = {p.default}" if p.default is not None else ""))
        tail = f" -> {self.returns}" if self.returns else ""
        return f"{self.name}({', '.join(parts)}){tail}"
