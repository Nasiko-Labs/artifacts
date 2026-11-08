"""Pure: the parsed-plan records shared by the text and JSON parsers, and the address splitter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tf_types import TAG_KEYS

UNKNOWN = "(known after apply)"
SENSITIVE = "(sensitive value)"
# Actions that change something (a read, a no-op or a pure move does not).
CHANGING = ("create", "update", "delete", "replace")

_SEGMENT = re.compile(r'[^.\[]+(?:\[(?:"[^"]*"|[^\]]*)\])?')


@dataclass(frozen=True)
class Attr:
    key: str                 # top-level attribute name
    before: str | None = None
    after: str | None = None
    forces: bool = False     # `# forces replacement` / in `replace_paths`


@dataclass(frozen=True)
class Change:
    address: str
    rtype: str
    name: str
    module_path: str = ""    # "module.db" / "module.net.module.sg[0]" / ""
    mode: str = "managed"    # managed | data
    action: str = "update"   # create | update | delete | replace | read | noop | move
    attrs: tuple[Attr, ...] = ()        # changed top-level attributes only
    moved_from: str = ""
    known: tuple[tuple[str, str], ...] = ()  # top-level plain string values seen (for identifiers)

    @property
    def changed_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(a.key for a in self.attrs))

    @property
    def tag_only(self) -> bool:
        keys = set(self.changed_keys)
        return self.action == "update" and bool(keys) and keys <= TAG_KEYS

    @property
    def module_name(self) -> str:
        """The outermost module call name (`module.db.module.x` → `db`), or ""."""
        if not self.module_path:
            return ""
        first = self.module_path.split(".")[1] if "." in self.module_path else ""
        return first.split("[")[0]

    @property
    def resource_ref(self) -> str:
        return f"{self.rtype}.{self.name}"

    def attr(self, key: str) -> Attr | None:
        return next((a for a in self.attrs if a.key == key), None)


@dataclass(frozen=True)
class ParsedPlan:
    format: str                               # text | json
    changes: tuple[Change, ...] = ()
    stated: dict[str, int] | None = None      # the plan's own `Plan:` counts
    workspace: str = ""
    notes: tuple[str, ...] = field(default=())

    @property
    def changing(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.action in CHANGING)

    def counts(self) -> dict[str, int]:
        """What the parsed changes add up to, in the `Plan:` line's terms (a replace is one add and one destroy)."""
        acts = [c.action for c in self.changes]
        return {"add": acts.count("create") + acts.count("replace"), "change": acts.count("update"),
                "destroy": acts.count("delete") + acts.count("replace")}

    @property
    def mismatch(self) -> bool:
        if self.stated is None:
            return False
        parsed = self.counts()
        return any(self.stated.get(k, parsed[k]) != parsed[k] for k in parsed)


def split_address(address: str) -> tuple[str, str, str, str]:
    """`module.a[0].module.b.aws_x.y["k"]` → (module path, mode, type, name with index)."""
    segments = _SEGMENT.findall(address)
    modules: list[str] = []
    i = 0
    while i + 1 < len(segments) and segments[i] == "module":
        modules.append(f"module.{segments[i + 1]}")
        i += 2
    rest = segments[i:]
    mode = "managed"
    if rest and rest[0] == "data":
        mode, rest = "data", rest[1:]
    rtype = rest[0] if rest else ""
    name = ".".join(rest[1:]) if len(rest) > 1 else ""
    return ".".join(modules), mode, rtype, name


def bare_name(name: str) -> str:
    """`orders["a"]` / `orders[0]` → `orders`."""
    return name.split("[", 1)[0]
