"""Pure data: a parsed trace. Frames are always stored innermost first, whatever order the language prints them
in, so "the innermost app frame" means the same thing for every parser.

A trace is one or more exception blocks (a chain). `root` is the index of the block that caused the others (Python:
the first block; Java: the last `Caused by`; C#: the innermost `--->`), `surface` the one the program died with."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Frame:
    file: str
    line: int
    func: str = ""
    col: int | None = None
    source: str = ""          # the source line the runtime printed under the frame (Python), if any
    origin: str = "app"       # app | library (set by frames.classify)
    repeat: int = 1           # consecutive identical frames collapsed into this one

    def label(self) -> str:
        """`file:line (func)`, the one form a location is ever shown in."""
        where = f"{self.file}:{self.line}"
        return f"{where} ({self.func})" if self.func else where

    def key(self) -> tuple[str, int, str]:
        return self.file, self.line, self.func


@dataclass(frozen=True)
class Block:
    exception_type: str
    message: str
    frames: tuple[Frame, ...] = ()

    def headline(self) -> str:
        return f"{self.exception_type}: {self.message}" if self.message else self.exception_type


@dataclass(frozen=True)
class Trace:
    language: str
    blocks: tuple[Block, ...]
    raw: str
    root: int = 0
    surface: int = 0
    frameless: bool = False
    omitted_repeats: int = 0  # Python's "[Previous line repeated N more times]", already added to a frame's repeat
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def root_block(self) -> Block:
        return self.blocks[self.root]

    @property
    def surface_block(self) -> Block:
        return self.blocks[self.surface]

    @property
    def exception_type(self) -> str:
        return self.surface_block.exception_type

    @property
    def message(self) -> str:
        return self.surface_block.message

    def chain(self) -> list[str]:
        """Every block's headline, root cause first, when there is more than one block."""
        if len(self.blocks) < 2:
            return []
        order = [self.root, *(i for i in range(len(self.blocks)) if i != self.root)]
        if self.surface != self.root:
            order = [self.root, *(i for i in order[1:] if i != self.surface), self.surface]
        return [self.blocks[i].headline() for i in order]

    def all_frames(self) -> list[Frame]:
        return [f for b in self.blocks for f in b.frames]

    def with_blocks(self, blocks: tuple[Block, ...]) -> Trace:
        return replace(self, blocks=blocks)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Trace:
        blocks = tuple(Block(b["exception_type"], b["message"],
                             tuple(Frame(**{**f, "line": int(f["line"]), "repeat": int(f.get("repeat") or 1)})
                                   for f in b.get("frames") or ()))
                       for b in data["blocks"])
        return Trace(language=data["language"], blocks=blocks, raw=data["raw"], root=int(data.get("root") or 0),
                     surface=int(data.get("surface") or 0), frameless=bool(data.get("frameless")),
                     omitted_repeats=int(data.get("omitted_repeats") or 0), notes=tuple(data.get("notes") or ()))
