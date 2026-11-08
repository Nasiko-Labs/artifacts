"""The two tool steps: locate a spreadsheet by title (a bounded tool loop, matches checked verbatim against
tool output) and read its values in row blocks (a bounded tool loop whose value results are captured by
code). The model never sees the cell values: a read result that carries a value grid is replaced, for the
model, by a short summary of how many rows were captured and the next range to read. So no cell text can
steer it, and no number passes through it."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import coverage
import prompts
import spec as specs
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, classify
from fence import FencedGateway
from headers import SheetRow
from nasiko_mcp import McpTool, ToolOk, ToolResult
from tool_health import ToolHealth, failure_text
from tool_loop import run_tool_loop

log = logging.getLogger("metrics_pull.sheet")

SOURCE = "spreadsheet"
LOCATE_CALLS = 2
READ_CALLS = 6
BLOCK_ROWS = 500
MAX_ROWS = 5000
MAX_MATCHES = 10
LLM_TIMEOUT_SECS = 45.0
_RANGE = re.compile(r"^(?:'?(?P<tab>.+?)'?!)?\$?(?P<c0>[A-Z]{1,3})?\$?(?P<r0>\d+)?"
                    r"(?::\$?(?P<c1>[A-Z]{1,3})?\$?(?P<r1>\d+)?)?$")


@dataclass(frozen=True)
class SheetRef:
    id: str
    name: str
    tabs: tuple[str, ...] = ()
    modified: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "tabs": list(self.tabs), "modified": self.modified}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> SheetRef:
        return SheetRef(str(data.get("id") or ""), str(data.get("name") or ""), tuple(data.get("tabs") or ()),
                        str(data.get("modified") or ""))


@dataclass(frozen=True)
class Reader:
    """One turn's read side: the spreadsheet tools, offered only to the steps of this module."""

    tools: tuple[McpTool, ...]
    catalog: Catalog
    deps: TurnDeps

    @staticmethod
    def of(deps: TurnDeps, catalog: Catalog) -> Reader:
        """Only tools whose own name or description says spreadsheet: semantic search returns its nearest
        tools even when none fits the query (a file store, a calendar), so search provenance is not enough."""
        own = tuple(t for t in catalog.read_tools() if classify(t) == SOURCE or t.name == RECOVER_META)
        has_own = any(t.name != RECOVER_META for t in own)
        return Reader(own if has_own else (), catalog, deps)

    def status(self, health: ToolHealth) -> coverage.SourceStatus:
        return coverage.assess(SOURCE, self.tools, classify, health.dropped, frozenset({RECOVER_META}))

    async def loop(self, health: ToolHealth, messages: Sequence[Mapping[str, Any]], max_iterations: int):
        return await run_tool_loop(self.deps.llm, self.deps.model, messages, self.tools, health,
                                   self.deps.traceparent, max_iterations=max_iterations,
                                   is_offered=health.is_offered, timeout=LLM_TIMEOUT_SECS)


# --- locate -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Located:
    matches: tuple[SheetRef, ...] = ()
    coverage: str = ""           # one line when the spreadsheet source could not be read


async def locate(reader: Reader, title: str) -> Located:
    fenced = FencedGateway(reader.deps.gateway)
    health = ToolHealth(fenced)
    messages = ({"role": "system", "content": prompts.locate_system(title)},
                {"role": "user", "content": f"Find the spreadsheet titled: {title}"})
    result = await reader.loop(health, messages, LOCATE_CALLS + 1)
    status = reader.status(health)
    if not status.ok:
        return Located((), coverage.line(status))
    data = specs.load_answer(result.text) if result.status == "completed" else None
    raw = data.get("matches") if data else None
    if not isinstance(raw, list):
        return Located()
    seen = checks.normalise_text(fenced.seen_text)
    found: dict[str, SheetRef] = {}
    for item in raw:
        ref = _checked_ref(item, seen)
        if ref is not None:
            found.setdefault(ref.id, ref)
    return Located(tuple(found.values())[:MAX_MATCHES])


def _checked_ref(item: Any, seen: str) -> SheetRef | None:
    if not isinstance(item, Mapping):
        return None
    sid, name = checks.text_of(item.get("id")), checks.text_of(item.get("name"))
    if not (checks.appears_in(sid, seen) and checks.appears_in(name, seen)):
        return None  # an id or title no tool returned this turn is dropped
    tabs = item.get("tabs") if isinstance(item.get("tabs"), list) else []
    modified = checks.text_of(item.get("modified"))
    return SheetRef(sid, name, tuple(checks.text_of(t) for t in tabs if checks.appears_in(t, seen)),
                    modified if checks.appears_in(modified, seen) else "")


def best_match(matches: Sequence[SheetRef], title: str) -> tuple[SheetRef | None, tuple[SheetRef, ...]]:
    """(the one sheet to read, or None with the candidates to ask about)."""
    wanted = {title_key(title)}
    exact = [m for m in matches if title_key(m.name) in wanted]
    if len(exact) == 1:
        return exact[0], ()
    if len(matches) == 1:
        return matches[0], ()
    return None, tuple(exact or matches)


def title_key(text: str) -> str:
    key = checks.normalise_text(text)
    for suffix in (" spreadsheet", " sheet"):
        key = key.removesuffix(suffix)
    return key


# --- read -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    tab: str
    first_row: int
    first_col: str
    last_col: str
    rows: tuple[tuple[object, ...], ...]


class GridCapture:
    """A gateway adapter for the read step: value grids are kept by code; the model gets a summary."""

    def __init__(self, gateway: Any, default_tab: str):
        self._gateway = gateway
        self._default_tab = default_tab
        self._blocks: tuple[Block, ...] = ()
        self._seen: tuple[str, ...] = ()

    @property
    def blocks(self) -> tuple[Block, ...]:
        return self._blocks

    @property
    def seen_text(self) -> str:
        return "\n".join(self._seen)

    def row_count(self) -> int:
        return sum(len(b.rows) for b in self._blocks)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk) or failure_text(result) is not None:
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        grids = extract_grids(body)
        if not grids:
            self._seen = (*self._seen, body)
            return ToolOk(({"type": "text", "text": prompts.fence(body[:12_000])},), result.is_error, result.raw)
        added = 0
        for cell_range, rows in grids:
            block = self._block(cell_range, rows)
            if block is not None:
                self._blocks = (*self._blocks, block)
                added += len(block.rows)
        summary = self._summary(added)
        return ToolOk(({"type": "text", "text": json.dumps(summary)},), False, result.raw)

    def _block(self, cell_range: str, rows: Sequence[Sequence[object]]) -> Block | None:
        m = _RANGE.match(cell_range.strip()) if cell_range else None
        covered = max((b.first_row + len(b.rows) - 1 for b in self._blocks), default=0)
        first_row = int(m.group("r0")) if m and m.group("r0") else covered + 1
        if first_row <= covered:
            return None  # the same rows again
        width = max((len(r) for r in rows), default=0)
        first_col = (m.group("c0") if m and m.group("c0") else "A")
        last_col = (m.group("c1") if m and m.group("c1") else col_letter(col_number(first_col) + max(width, 1) - 1))
        tab = (m.group("tab") if m and m.group("tab") else "") or self._default_tab
        return Block(tab, first_row, first_col, last_col, tuple(tuple(r) for r in rows))

    def _summary(self, added: int) -> dict:
        total = self.row_count()
        last = self._blocks[-1] if self._blocks else None
        if total > MAX_ROWS:
            return {"captured_rows": added, "total_captured": total, "status": "stop: enough rows read"}
        if last is None or added < BLOCK_ROWS:
            return {"captured_rows": added, "total_captured": total, "status": "complete: reply DONE"}
        nxt = last.first_row + len(last.rows)
        tab = f"'{last.tab}'!" if last.tab else ""
        return {"captured_rows": added, "total_captured": total, "status": "more rows may exist",
                "next_range": f"{tab}{last.first_col}{nxt}:{last.last_col}{nxt + BLOCK_ROWS - 1}"}


@dataclass(frozen=True)
class ReadOutcome:
    rows: tuple[SheetRow, ...] = ()
    tab: str = ""
    cell_range: str = ""
    capped: bool = False          # more than MAX_ROWS data rows: only the first MAX_ROWS kept
    partial: bool = False         # the read budget ended while more rows may exist
    coverage: str = ""


async def read(reader: Reader, ref: SheetRef, tab: str, cell_range: str) -> ReadOutcome:
    capture = GridCapture(reader.deps.gateway, tab)
    health = ToolHealth(capture)
    system = prompts.read_system(ref.id, ref.name, tab, cell_range, BLOCK_ROWS)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": "Read the values."})
    result = await reader.loop(health, messages, READ_CALLS + 1)
    status = reader.status(health)
    if not capture.blocks:
        return ReadOutcome(coverage="" if status.ok else coverage.line(status))
    rows = tuple(SheetRow(b.first_row + i, tuple(r)) for b in capture.blocks for i, r in enumerate(b.rows))
    capped = len(rows) > MAX_ROWS + 1  # + the header row
    rows = rows[:MAX_ROWS + 1]
    first = capture.blocks[0]
    width = max(len(r.cells) for r in rows)
    last_col = col_letter(col_number(first.first_col) + width - 1)
    span = f"{first.first_col}{first.first_row}:{last_col}{rows[-1].number}"
    partial = result.status == "max_iterations" and not capped
    return ReadOutcome(rows, first.tab or tab, span, capped, partial)


def extract_grids(body: str) -> list[tuple[str, list[list[object]]]]:
    """Every `values` grid (a list of rows) in a JSON tool result, with the `range` beside it."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    found: list[tuple[str, list[list[object]]]] = []
    _walk(data, found, 0)
    return found


def _walk(node: Any, found: list, depth: int) -> None:
    if depth > 6:
        return
    if isinstance(node, Mapping):
        values = node.get("values")
        if isinstance(values, list) and values and all(isinstance(r, list) for r in values):
            found.append((str(node.get("range") or ""), [list(r) for r in values]))
            return
        for child in node.values():
            _walk(child, found, depth + 1)
    elif isinstance(node, list):
        for child in node:
            _walk(child, found, depth + 1)


def col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return max(n, 1)


def col_letter(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out or "A"
