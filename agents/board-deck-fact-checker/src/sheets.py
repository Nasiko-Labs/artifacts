"""The source spreadsheets: find candidates (one bounded tool loop), then read them (another). Cell values
are taken in code from the spreadsheet tools' own output (every `values` grid with its range), never from
the model's transcription, so each cell reference and value in the report comes from this turn's tool output.

Caps: at most `MAX_SHEETS` spreadsheets, `MAX_TABS` tabs and `cellindex.MAX_CELLS_PER_TAB` cells per tab, each
stated when hit; hidden tabs that were not read are listed as gaps.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import cellindex
import checks
import prompts
from deps import TurnDeps
from discovery import Catalog
from records import Cell
from request import SourceRow
from steps import StepResult, run_step

log = logging.getLogger("board_deck.sheets")

FIND_LOOP_CALLS = 3
READ_LOOP_CALLS = 6  # plus one repair call
MAX_SHEETS = 5
MAX_TABS = 8
MAX_CANDIDATES = 10
PASTED_NAME = "Pasted source rows"


@dataclass(frozen=True)
class Target:
    name: str
    id: str = ""
    url: str = ""
    owner: str = ""
    modified: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "id": self.id, "url": self.url, "owner": self.owner, "modified": self.modified}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Target:
        return Target(**{k: str(data.get(k) or "") for k in ("name", "id", "url", "owner", "modified")})


@dataclass(frozen=True)
class SheetRead:
    status: str  # ok | no_tools | unreadable | paused | skipped | empty
    tabs: tuple[cellindex.Tab, ...] = ()
    names: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    dropped: Mapping[str, str] = field(default_factory=dict)


async def find(deps: TurnDeps, catalog: Catalog, query: str, hints: Sequence[str]) -> tuple[StepResult,
                                                                                               tuple[Target, ...]]:
    step = await run_step(deps, catalog.tools_for("sheet"), catalog.writes, prompts.sheets_find_system(hints),
                          query, FIND_LOOP_CALLS)
    if step.status != "ok":
        return step, ()
    seen = step.seen_text
    found: list[Target] = []
    for item in checks.items(step.answer, "candidates"):
        name = checks.text_of(item.get("name"))
        if not name or name not in seen or any(t.name == name for t in found):
            continue
        fields = {k: checks.text_of(item.get(k)) for k in ("id", "url", "owner", "modified")}
        found.append(Target(name, **{k: v if v and v in seen else "" for k, v in fields.items()}))
    return step, tuple(found[:MAX_CANDIDATES])


def choose(candidates: Sequence[Target], message: str) -> tuple[Target, ...] | None:
    """The targets to read without asking, or None when the user must pick one."""
    named = tuple(t for t in candidates if t.name.casefold() in (message or "").casefold())
    if named:
        return named[:MAX_SHEETS]
    if len(candidates) <= 1:
        return tuple(candidates)
    return None


def pick(answer: str, candidates: Sequence[Target]) -> Target | None:
    """The single-select answer: a picked label, a typed name, or a typed link."""
    text = (answer or "").strip()
    for target in candidates:
        if text.casefold() == target.name.casefold():
            return target
    if text.startswith(("http://", "https://")):
        return Target(name=text, url=text)
    return next((t for t in candidates if t.name.casefold() in text.casefold()), None)


async def read(deps: TurnDeps, catalog: Catalog, query: str, targets: Sequence[Target]) -> SheetRead:
    wanted = tuple(targets[:MAX_SHEETS])
    notes = (f"Only the first {MAX_SHEETS} spreadsheets were read.",) if len(targets) > MAX_SHEETS else ()
    system = prompts.sheets_read_system([t.to_dict() for t in wanted])
    step = await run_step(deps, catalog.tools_for("sheet"), catalog.writes, system, query, READ_LOOP_CALLS)
    if step.status in ("no_tools", "paused", "skipped", "off_topic"):
        return SheetRead(step.status, notes=notes, dropped=step.dropped)
    parsed = parse_results(step.results, step.answer, wanted)
    extra = (prompts.STEP_LIMIT_NOTE.format(what="the spreadsheets"),) if step.partial else ()
    status = "ok" if parsed.tabs else "empty"
    return SheetRead(status, parsed.tabs, parsed.names, (*notes, *parsed.notes, *extra), step.dropped)


# --- parsing the spreadsheet tools' output -------------------------------------------------------------

@dataclass(frozen=True)
class _Grid:
    range_text: str
    values: list
    sheet_id: str


def parse_results(results: Sequence[tuple[str, str]], answer: Mapping[str, Any],
                  targets: Sequence[Target]) -> SheetRead:
    titles: dict[str, str] = {}
    urls: dict[str, str] = {}
    hidden: list[str] = []
    grids: list[_Grid] = []
    for _, body in results:
        try:
            data = json.loads(body)
        except ValueError:
            continue
        for kind, payload in _walk(data, ""):
            if kind == "grid":
                grids.append(payload)
            elif kind == "title":
                titles.setdefault(payload[0], payload[1])
            elif kind == "url":
                urls.setdefault(payload[0], payload[1])
            elif kind == "hidden":
                hidden.append(payload)
    return _tabs(grids, titles, urls, hidden, answer, targets, "\n".join(b for _, b in results))


def _walk(value: Any, sheet_id: str) -> Iterator[tuple[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _walk(item, sheet_id)
        return
    if not isinstance(value, dict):
        return
    sid = value.get("spreadsheetId") if isinstance(value.get("spreadsheetId"), str) else sheet_id
    props = value.get("properties") if isinstance(value.get("properties"), dict) else {}
    if "spreadsheetId" in value and isinstance(props.get("title"), str):
        yield "title", (sid, props["title"])
    if isinstance(value.get("spreadsheetUrl"), str):
        yield "url", (sid, value["spreadsheetUrl"])
    if props.get("hidden") is True and isinstance(props.get("title"), str):
        yield "hidden", props["title"]
    for key in ("values", "rows"):
        grid = value.get(key)
        if isinstance(grid, list) and grid and all(isinstance(r, list) for r in grid):
            yield "grid", _Grid(str(value.get("range") or value.get("sheet") or ""), grid, sid)
    for key, item in value.items():
        if key not in ("values", "rows") and isinstance(item, (dict, list)):
            yield from _walk(item, sid)


def _tabs(grids: Sequence[_Grid], titles: Mapping[str, str], urls: Mapping[str, str], hidden: Sequence[str],
          answer: Mapping[str, Any], targets: Sequence[Target], seen: str) -> SheetRead:
    named = [i for i in checks.items(answer, "spreadsheets") if checks.text_of(i.get("name")) in seen]
    tabs: list[cellindex.Tab] = []
    keys: set[tuple[str, str]] = set()
    notes: list[str] = []
    for grid in grids:
        if (grid.sheet_id, grid.range_text) in keys:
            continue
        keys.add((grid.sheet_id, grid.range_text))
        if len(tabs) >= MAX_TABS:
            notes.append(f"Tab cap reached: only {MAX_TABS} tabs were read.")
            break
        name, url = _identity(grid.sheet_id, titles, urls, named, targets, seen)
        tab_name, row, col = cellindex.origin(grid.range_text)
        rows, cut = cellindex.cap(grid.values)
        tab = cellindex.Tab(name, tab_name or "Sheet1", rows, url, row, col, cut)
        if cut:
            notes.append(f"Tab {tab.tab} of {name} was cut at {cellindex.MAX_CELLS_PER_TAB:,} cells; name a range "
                         "to check the rest.")
        tabs.append(tab)
    read_tabs = {t.tab for t in tabs}
    notes.extend(f"Tab {h} is hidden and was not read." for h in dict.fromkeys(hidden) if h not in read_tabs)
    skipped = [s for s in (checks.text_of(x) for x in (answer.get("tabs_skipped") or [])
                           if isinstance(answer.get("tabs_skipped"), list)) if s and s in seen]
    if skipped:
        notes.append(f"Tabs skipped as not holding metrics: {', '.join(skipped)}.")
    names = tuple(dict.fromkeys(t.spreadsheet for t in tabs))
    return SheetRead("ok" if tabs else "empty", tuple(tabs), names, tuple(dict.fromkeys(notes)))


def _identity(sheet_id: str, titles: Mapping[str, str], urls: Mapping[str, str],
              named: Sequence[Mapping[str, Any]], targets: Sequence[Target], seen: str) -> tuple[str, str]:
    by_id = next((i for i in named if checks.text_of(i.get("id")) == sheet_id and sheet_id), None)
    only = named[0] if len(named) == 1 else None
    entry = by_id or only or {}
    target = next((t for t in targets if t.id and t.id == sheet_id), None) or \
        (targets[0] if len(targets) == 1 else None)
    name = titles.get(sheet_id) or checks.text_of(entry.get("name")) or (target.name if target else "") \
        or sheet_id or "spreadsheet"
    answer_url = checks.text_of(entry.get("url"))
    url = urls.get(sheet_id) or (answer_url if answer_url and answer_url in seen else "") \
        or (target.url if target and target.url.startswith("http") else "")
    return name, url


def pasted_cells(rows: Sequence[SourceRow]) -> tuple[Cell, ...]:
    """Pasted `metric, period, value` rows as cells (address `Pasted!line N`), scale from the value's own
    suffix or the metric label."""
    cells: list[Cell] = []
    for row in rows:
        tab = cellindex.Tab(PASTED_NAME, "Pasted", ((row.metric, row.value),))
        indexed = cellindex.index_tab(tab, len(cells))
        if not indexed:
            continue
        cell = indexed[0]
        cells.append(Cell(**{**cell.to_dict(), "address": f"Pasted line {row.line}", "row_label": row.metric,
                              "col_label": row.period}))
    return tuple(cells)
