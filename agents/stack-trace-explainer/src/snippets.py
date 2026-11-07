"""Pure: tie pasted code blocks to trace frames and build the numbered views (`L41: ...`) the model cites.

A block is tied to a frame by its label (same file), else, when unlabelled, by the frame's function name found in
it (`def total(`, `function total(`). Numbering: an explicit `lines 40-70` wins; else, when the runtime printed the
frame's source line and that line is in the block, the block is aligned so it sits at the frame's line number;
else numbering starts at 1 and the reply says so."""

from __future__ import annotations

import re
from dataclasses import dataclass

from paste import CodeBlock
from trace_model import Frame, Trace

WINDOW = 12
MAX_VIEW_LINES = 60
MAX_VIEWS = 4


@dataclass(frozen=True)
class CodeView:
    path: str
    start: int                  # number of lines[0]
    lines: tuple[str, ...]      # the whole (aligned) block
    focus: int | None = None    # the frame line this view is about, when it is inside the block
    numbered_from_one: bool = False

    def has_line(self, number: int) -> bool:
        return self.start <= number < self.start + len(self.lines)

    def line(self, number: int) -> str:
        return self.lines[number - self.start] if self.has_line(number) else ""

    def render(self) -> str:
        if self.focus is not None:
            first = max(self.start, self.focus - WINDOW)
            last = min(self.start + len(self.lines) - 1, self.focus + WINDOW)
        else:
            first, last = self.start, min(self.start + len(self.lines) - 1, self.start + MAX_VIEW_LINES - 1)
        numbered = [f"L{n}: {self.line(n)}" for n in range(first, last + 1)]
        return "\n".join(numbered)


def short_name(func: str) -> str:
    """`com.acme.Repo.findById` / `main.(*Server).handle` / `Layer.handle [as x]` -> the bare function name."""
    name = re.sub(r"\s*\[as [^\]]+\]$", "", func or "").strip()
    name = re.split(r"[.:#/]", name)[-1] if name else ""
    return name.strip("()*<>$ ")


def same_file(block_path: str, frame_file: str) -> bool:
    a = block_path.replace("\\", "/").lstrip("./").strip()
    b = frame_file.replace("\\", "/").lstrip("./").strip()
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a) or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def defines(block: CodeBlock, func: str) -> int | None:
    """Index of the line in `block` that defines `func`, if any."""
    name = short_name(func)
    if not name or not re.fullmatch(r"\w+", name):
        return None
    pattern = re.compile(rf"\b(?:def|async def|function|func|fn|sub)\s+(?:\([^)]*\)\s*)?{re.escape(name)}\s*[(<]|"
                         rf"\b{re.escape(name)}\s*\([^)]*\)\s*(?:\{{|=>|throws\b|:\s*$)|\b{re.escape(name)}\s*=\s*"
                         rf"(?:async\s*)?(?:function\b|\()")
    return next((i for i, line in enumerate(block.lines) if pattern.search(line)), None)


def _source_index(block: CodeBlock, frame: Frame) -> int | None:
    wanted = " ".join(frame.source.split())
    if not wanted:
        return None
    return next((i for i, line in enumerate(block.lines) if " ".join(line.split()) == wanted), None)


def view_for(block: CodeBlock, frame: Frame | None) -> CodeView:
    start, from_one = block.start, not block.explicit_start
    if frame is not None and not block.explicit_start:
        index = _source_index(block, frame)
        if index is not None and frame.line - index >= 1:
            start, from_one = frame.line - index, False
    path = block.path or (frame.file if frame is not None else "")
    view = CodeView(path, start, block.lines, None, from_one)
    focus = frame.line if frame is not None and view.has_line(frame.line) and not from_one else None
    return CodeView(path, start, block.lines, focus, from_one)


def tie(trace: Trace, blocks: tuple[CodeBlock, ...], candidate: Frame | None) -> list[CodeView]:
    """One view per pasted block, the candidate's first. A block is tied to the candidate frame when it names the
    same file or (unlabelled) defines the candidate's function; else to any app frame of the trace it names."""
    views: list[tuple[int, CodeView]] = []
    app = [f for f in trace.all_frames() if f.origin == "app"]
    for block in blocks:
        frame = _frame_for(block, candidate, app)
        rank = 0 if frame is not None and frame == candidate else 1
        views.append((rank, view_for(block, frame)))
    views.sort(key=lambda pair: pair[0])
    return [v for _, v in views][:MAX_VIEWS]


def _frame_for(block: CodeBlock, candidate: Frame | None, app: list[Frame]) -> Frame | None:
    if block.path:
        if candidate is not None and same_file(block.path, candidate.file):
            return candidate
        return next((f for f in app if same_file(block.path, f.file)), None)
    if candidate is not None and (defines(block, candidate.func) is not None or _source_index(block, candidate)
                                  is not None):
        return candidate
    return next((f for f in app if defines(block, f.func) is not None), None)


def candidate_view(views: list[CodeView], frame: Frame | None) -> CodeView | None:
    """The view that holds the candidate frame's line, when pasted code covers it."""
    if frame is None:
        return None
    return next((v for v in views if v.focus == frame.line and (not v.path or same_file(v.path, frame.file))), None)
