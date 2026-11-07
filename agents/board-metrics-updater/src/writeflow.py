"""Prepare the writes for an approved report: find the sheet (or doc), map its header, pick the write
tool, and build one Write per row (per currency) or the one paragraph. Asks a single-select question
when the target is not named or the header matches nothing. Never writes: the walk does that, one
approval per write. Without a write tool the row or paragraph is given as text to paste."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import prompts
import sheetmap
import steps
import walk
from deps import TurnDeps
from discovery import discover
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from nasiko_mcp import McpTool
from render import Report
from send_guard import DESTRUCTIVE, name_tokens
from writes import Write

PICK_KEY = "board_metrics.pick"
ORDER_KEY = "board_metrics.default_order"
APPEND_DEFAULT = "Append in the default order"
SKIP_DEFAULT = "Skip"
_APPEND_WORDS = frozenset({"append", "insert", "add"})
_PASSTHROUGH = (prompts.NO_TRACE_CONTEXT, prompts.GATEWAY_UNREACHABLE)


@dataclass(frozen=True)
class Flow:
    reply: str = ""
    writes: tuple[Write, ...] = ()
    question: dict | None = None
    remember: Mapping[str, Any] = field(default_factory=dict)


async def run(deps: TurnDeps, report: Report, kind: str, name: str | None, outcomes: Mapping[str, str], *,
              target: Mapping[str, Any] | None = None, default_order: bool = False) -> Flow:
    if not report.blocks:
        return Flow(prompts.NO_METRICS_YET)
    pending = [b for b in report.blocks if outcomes.get(_key(kind, b.currency, report)) != "written"]
    if not pending:
        return Flow(f"That {'row' if kind == 'sheet' else 'paragraph'} was already written in this conversation.")
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return Flow(failure if failure in _PASSTHROUGH else _fallback(kind, None, report, _no_tool(kind)))
    reader = steps.reader_for(deps, catalog)
    source = "spreadsheet" if kind == "sheet" else "doc"
    if not reader.tools_for(source):
        return Flow(_fallback(kind, name, report, _no_tool(kind)))
    found = steps.Target(**_target_args(target)) if target else None
    if found is None and not name:
        return await _pick(deps, reader, kind, report)
    found = found or await steps.open_target(deps, reader, kind, name)
    if found is None:
        return Flow(_fallback(kind, name, report, prompts.not_found("sheet" if kind == "sheet" else "document",
                                                                    name or "")))
    remember = {"sheet_name" if kind == "sheet" else "doc_name": found.name}
    keys = sheetmap.map_header(found.header) if kind == "sheet" else ()
    if kind == "sheet" and not default_order and not sheetmap.has_match(keys):
        return Flow(question=_order_question(found), remember=remember)
    tool = await _write_tool(deps, kind)
    if tool is None:
        reason = prompts.NO_SHEET_WRITE if kind == "sheet" else prompts.NO_DOC_WRITE
        return Flow(_fallback(kind, found.name, report, reason), remember=remember)
    if kind == "doc":
        return Flow(writes=(_doc_write(tool, found, report),), remember=remember)
    if default_order or not sheetmap.has_match(keys):
        keys, header = sheetmap.DEFAULT_KEYS, sheetmap.DEFAULT_HEADER
    else:
        header = found.header
    writes = tuple(Write(kind="sheet", key=_key("sheet", b.currency, report), tool=tool.name, target_id=found.id,
                         target_name=found.name, tab=found.tab, header=tuple(header),
                         values=sheetmap.row(keys, b, report)) for b in pending)
    return Flow(writes=writes, remember=remember)


def _key(kind: str, currency: str, report: Report) -> str:
    return f"{kind}:{report.label}" + (f":{currency}" if kind == "sheet" else "")


def _target_args(target: Mapping[str, Any]) -> dict:
    return {"id": str(target.get("id") or ""), "name": str(target.get("name") or ""),
            "tab": str(target.get("tab") or ""), "header": tuple(str(h) for h in target.get("header") or ())}


def _no_tool(kind: str) -> str:
    return prompts.NO_SHEET_TOOL if kind == "sheet" else prompts.NO_DOC_TOOL


def _doc_write(tool: McpTool, found: steps.Target, report: Report) -> Write:
    return Write(kind="doc", key=_key("doc", "", report), tool=tool.name, target_id=found.id,
                 target_name=found.name, text=report.narrative)


async def _write_tool(deps: TurnDeps, kind: str) -> McpTool | None:
    """An append tool for the sheet, or an append/insert tool for the doc (never one that replaces)."""
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        return None
    source = "spreadsheet" if kind == "sheet" else "doc"
    tools = [t for t in catalog.send_tools(source) if not DESTRUCTIVE & set(name_tokens(t.name))]
    appending = [t for t in tools if _APPEND_WORDS & set(name_tokens(t.name))]
    if kind == "doc":
        return appending[0] if appending else None
    return (appending or tools or [None])[0]


async def _pick(deps: TurnDeps, reader: steps.Reader, kind: str, report: Report) -> Flow:
    found = await steps.list_targets(deps, reader, kind)
    noun = "sheet" if kind == "sheet" else "doc"
    if not found:
        return Flow(f'Which {noun} should I use? Say "add it to {noun} <name>".')
    header = prompts.SHEET_HEADER if kind == "sheet" else prompts.DOC_HEADER
    labels = {_label(t.name): t.name for t in found}
    hitl = question_metadata(header, [(label, "") for label in labels], multi_select=False, allow_custom_input=True)
    text = f"{header} Pick one, or type its name."
    return Flow(question={"text": text, "hitl": hitl, "metadata": {PICK_KEY: {"kind": kind, "names": labels}}})


def _label(name: str) -> str:
    return name.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def _order_question(found: steps.Target) -> dict:
    header = prompts.DEFAULT_ORDER_HEADER
    columns = ", ".join(sheetmap.DEFAULT_HEADER)
    seen = ", ".join(found.header) if found.header else "an empty first row"
    text = (f"None of the columns in {found.name} ({seen}) match a metric I know. {header} The columns would be: "
            f"{columns}.")
    hitl = question_metadata(header, [(APPEND_DEFAULT, columns), (SKIP_DEFAULT, "Don't add the row.")],
                             multi_select=False, allow_custom_input=False)
    target = {"id": found.id, "name": found.name, "tab": found.tab, "header": list(found.header)}
    return {"text": text, "hitl": hitl, "metadata": {ORDER_KEY: {"target": target}}}


def _fallback(kind: str, name: str | None, report: Report, reason: str) -> str:
    """The reason, plus the row(s) or paragraph as text to paste."""
    target = name or ("your metrics sheet" if kind == "sheet" else "your board document")
    if kind == "doc":
        return f"{reason}\n\n" + walk.copyable(Write(kind="doc", key="", tool="", target_id="", target_name=target,
                                                     text=report.narrative))
    values = [sheetmap.row(sheetmap.DEFAULT_KEYS, b, report) for b in report.blocks]
    rows = [Write(kind="sheet", key="", tool="", target_id="", target_name=target,
                  header=sheetmap.DEFAULT_HEADER if i == 0 else (), values=v) for i, v in enumerate(values)]
    return f"{reason}\n\n" + "\n\n".join(walk.copyable(r) for r in rows)


def pick_name(answer: str, names: Mapping[str, str]) -> str:
    text = (answer or "").strip()
    return names.get(text) or next((v for k, v in names.items() if k.casefold() == text.casefold()), text)

