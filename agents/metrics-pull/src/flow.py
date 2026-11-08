"""One pull, start to end: source (pasted table, an envelope's table, or a sheet: locate -> read), header
map, spec (envelope spec, a stored spec, or one model call + at most one repair), validation, the verbatim
check, execution, rendering. Pauses (sheet name, sheet pick, column pick) come back as a question whose
metadata holds everything the resume needs. Nothing here keeps state: the graph remembers `memory`."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import checks
import dates
import engine
import headers
import models
import paste
import prompts
import render
import sheet
import smalltalk
import spec as specs
from deps import TurnDeps
from discovery import discover
from headers import HeaderMap, SheetRow
from nasiko_hitl import question_metadata
from parse import Request
from tool_loop import run_tool_loop

log = logging.getLogger("metrics_pull.flow")

LLM_TIMEOUT_SECS = 45.0
PAUSE_KEY = "pause"
_SKIP = re.compile(r"^\s*(?:skip|cancel|stop|never ?mind|no|none)\s*[.!]?\s*$", re.IGNORECASE)
_JSON_ONLY = re.compile(r"^\s*(?:(?:and|now|ok|please|give|it|me|return|show|the|same|that|this|as|in|output)\s+)*"
                        r"json(?:\s+only|\s+please)?\s*[.!?]?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TurnResult:
    reply: str
    question: dict | None = None      # {"text", "metadata" (task metadata), "hitl" (message metadata)}
    envelope: dict | None = None
    remember: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Resume:
    sheet: sheet.SheetRef | None = None
    tab: str = ""
    spec: Mapping[str, Any] | None = None
    spec_from_model: bool = True
    picks: Mapping[str, str] = field(default_factory=dict)


class _NoTools:
    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the kit loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), _NoTools(), deps.traceparent,
                                 max_iterations=1, timeout=LLM_TIMEOUT_SECS)
    return result.text.strip()


# --- entry points -----------------------------------------------------------------------------------------

async def run(deps: TurnDeps, req: Request, memory: Mapping[str, Any], resume: _Resume | None = None) -> TurnResult:
    resume = resume or _Resume()
    if req.why:
        return TurnResult(prompts.WHY_REPLY)
    if req.table_rows:
        rows = tuple(SheetRow(i + 1, tuple(r)) for i, r in enumerate(req.table_rows))
        return await _on_rows(deps, req, rows, models.Source("table from the previous step"), memory, resume,
                              models.Quality())
    if req.pasted:
        found = paste.find(req.pasted)
        rows = found.rows if found else ()
        return await _on_rows(deps, req, rows, models.Source("pasted table"), memory, resume,
                              models.Quality(pasted=True))
    if not deps.traceparent:
        return TurnResult(prompts.NO_TRACE_CONTEXT)
    if memory.get("spec") and _JSON_ONLY.match(req.text) and resume.spec is None:
        resume = replace(resume, spec=memory["spec"], spec_from_model=False)
    remembered = memory.get("source") or {}
    ref = resume.sheet or _remembered_ref(req, remembered)
    title = req.title
    if ref is None and not req.names_source:
        status, title = await _scope(deps, req)
        if status == prompts.OFF_TOPIC:
            return TurnResult(smalltalk.OFF_TOPIC_REPLY)
        if status == prompts.WHY:
            return TurnResult(prompts.WHY_REPLY)
        if not title:
            return _pause(prompts.ASK_SHEET, {"kind": "sheet_name", "request": req.to_dict()}, None)
        req = replace(req, title=title)
    return await _from_sheet(deps, req, ref, remembered, memory, resume)


async def resume_pause(deps: TurnDeps, pause: Mapping[str, Any], answer: str,
                       memory: Mapping[str, Any]) -> TurnResult:
    if _SKIP.match(answer or ""):
        return TurnResult(prompts.NOTHING_PULLED)
    req = Request.from_dict(pause.get("request") or {})
    kind = pause.get("kind")
    text = (answer or "").strip().strip("\"'“”")
    if kind == "sheet_name":
        return await run(deps, replace(req, title=text, sheet_id=""), memory)
    if kind == "sheet_pick":
        options = {str(k): v for k, v in (pause.get("options") or {}).items()}
        picked = options.get(text) or next((v for k, v in options.items() if k.casefold() == text.casefold()), None)
        if picked is None:
            return await run(deps, replace(req, title=text, sheet_id=""), memory)
        return await run(deps, req, memory, _Resume(sheet=sheet.SheetRef.from_dict(picked)))
    candidates = [str(c) for c in pause.get("candidates") or ()]
    chosen = next((c for c in candidates if c.casefold() == text.casefold()), text)
    picks = {**dict(pause.get("picks") or {}), str(pause.get("term")): chosen}
    ref = sheet.SheetRef.from_dict(pause["sheet"]) if pause.get("sheet") else None
    return await run(deps, req, memory, _Resume(sheet=ref, tab=str(pause.get("tab") or ""), spec=pause.get("spec"),
                                                spec_from_model=bool(pause.get("spec_from_model", True)),
                                                picks=picks))


# --- the sheet --------------------------------------------------------------------------------------------

def _remembered_ref(req: Request, remembered: Mapping[str, Any]) -> sheet.SheetRef | None:
    if not remembered.get("sheet"):
        return None
    ref = sheet.SheetRef.from_dict(remembered["sheet"])
    if req.sheet_id:
        return ref if req.sheet_id == ref.id else None
    if not req.title:
        return ref
    return ref if sheet.title_key(ref.name) == sheet.title_key(req.title) else None


async def _scope(deps: TurnDeps, req: Request) -> tuple[str, str]:
    """No sheet named and none remembered: one tool-less call says whether this is in scope and which sheet."""
    messages = ({"role": "system", "content": prompts.scope_system()}, {"role": "user", "content": req.text})
    data = specs.load_answer(await ask_once(deps, messages)) or {}
    status = str(data.get("status") or "").strip()
    title = checks.text_of(data.get("sheet"))
    if title and not checks.appears_in(title, checks.normalise_text(req.text)):
        title = ""  # a title the user never wrote is not used
    if status.upper() in (prompts.OFF_TOPIC, prompts.WHY):
        return status.upper(), ""
    return "ok", title


async def _from_sheet(deps: TurnDeps, req: Request, ref: sheet.SheetRef | None, remembered: Mapping[str, Any],
                      memory: Mapping[str, Any], resume: _Resume) -> TurnResult:
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return TurnResult(failure)
    reader = sheet.Reader.of(deps, catalog)
    if not reader.tools:
        return TurnResult(prompts.NO_SHEET_TOOL)
    if ref is None and req.sheet_id:
        ref = sheet.SheetRef(req.sheet_id, req.sheet_id)
    if ref is None:
        located = await sheet.locate(reader, req.title)
        if located.coverage:
            return TurnResult(f"{prompts.NO_SHEET_TOOL}\n\n{located.coverage}")
        ref, candidates = sheet.best_match(located.matches, req.title)
        if ref is None and candidates:
            return _sheet_pick(req, candidates)
        if ref is None:
            return TurnResult(prompts.NO_SHEET_FOUND.format(title=req.title))
    same_sheet = (remembered.get("sheet") or {}).get("id") == ref.id
    tab = resume.tab or req.tab or (str(remembered.get("tab") or "") if same_sheet else "")
    read = await sheet.read(reader, ref, tab, req.cell_range)
    if not read.rows:
        where = f' (tab "{tab}")' if tab else ""
        text = prompts.NO_VALUES.format(title=ref.name, where=where)
        return TurnResult(f"{text}\n\n{read.coverage}" if read.coverage else text)
    source = models.Source(ref.name, read.tab, read.cell_range, ref.modified)
    quality = models.Quality(capped=read.capped, partial=read.partial)
    notes: tuple[str, ...] = ()
    if read.capped:
        notes = (f"First {sheet.MAX_ROWS:,} rows read; the rest of the sheet was not.",)
    elif read.partial:
        notes = (prompts.STEP_LIMIT_NOTE.format(rows=len(read.rows) - 1),)
    keep = {"source": {"sheet": ref.to_dict(), "tab": read.tab or tab}}
    result = await _on_rows(deps, req, read.rows, source, memory, replace(resume, sheet=ref, tab=read.tab or tab),
                            quality, notes)
    return replace(result, remember={**keep, **dict(result.remember)})


def _sheet_pick(req: Request, candidates: Sequence[sheet.SheetRef]) -> TurnResult:
    options: dict[str, dict] = {}
    for ref in candidates[:sheet.MAX_MATCHES]:
        label = ref.name if ref.name not in options else f"{ref.name} ({ref.id[:12]})"
        options[label[:200]] = ref.to_dict()
    meta = question_metadata(prompts.SHEET_PICK_HEADER,
                             [(k, f"modified {v['modified']}" if v["modified"] else "") for k, v in options.items()],
                             allow_custom_input=True)
    text = f"Several spreadsheets match \"{req.title}\". Which one should I read?"
    return _pause(text, {"kind": "sheet_pick", "request": req.to_dict(), "options": options}, meta)


def _pause(text: str, pause: Mapping[str, Any], hitl: dict | None) -> TurnResult:
    return TurnResult("", {"text": text, "metadata": {PAUSE_KEY: dict(pause)}, "hitl": hitl})


# --- the table --------------------------------------------------------------------------------------------

async def _on_rows(deps: TurnDeps, req: Request, rows: Sequence[SheetRow], source: models.Source,
                   memory: Mapping[str, Any], resume: _Resume, quality: models.Quality,
                   notes: tuple[str, ...] = ()) -> TurnResult:
    table = headers.build(rows)
    if table is None or not table.rows:
        return TurnResult(f"I found no data rows in {source.title}.")
    ranges = dates.resolve(req.text, deps.today)
    raw, from_model = resume.spec or req.spec, resume.spec_from_model if resume.spec else False
    if raw is None:
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        answer = await _spec_call(deps, req, table, ranges, memory.get("spec"))
        if isinstance(answer, str):
            return TurnResult(answer)
        raw, from_model = answer, True
    checked = specs.validate(raw, table.names, resume.picks)
    if checked.errors and from_model and deps.traceparent:
        raw = await _repair(deps, req, table, ranges, raw, checked.errors) or raw
        checked = specs.validate(raw, table.names, resume.picks)
    if checked.errors:
        log.info("spec rejected: %s", checked.errors)
        return TurnResult(prompts.UNREADABLE_SPEC)
    if checked.ambiguous is not None:
        return _column_pick(req, checked.ambiguous, raw, from_model, resume)
    query = checked.spec
    dropped: tuple[str, ...] = ()
    if from_model:
        query, dropped = _verbatim(query, req, table, ranges)
    return _answer(deps, req, table, source, query, checked.missing, dropped, quality, notes, resume, ranges)


async def _spec_call(deps: TurnDeps, req: Request, table: HeaderMap, ranges: Sequence[dates.DateRange],
                     prior: Mapping[str, Any] | None) -> Mapping[str, Any] | str:
    columns = [c.to_dict() for c in table.columns]
    system = prompts.spec_system(columns, [r.to_dict() for r in ranges], prior)
    data = specs.load_answer(await ask_once(deps, ({"role": "system", "content": system},
                                                   {"role": "user", "content": req.text or "Show the table."})))
    if data is None:
        return prompts.UNREADABLE_SPEC
    status = str(data.get("status") or "ok").upper()
    if status == prompts.OFF_TOPIC:
        return smalltalk.OFF_TOPIC_REPLY
    if status == prompts.WHY:
        return prompts.WHY_REPLY
    raw = data.get("spec")
    return raw if isinstance(raw, Mapping) else prompts.UNREADABLE_SPEC


async def _repair(deps: TurnDeps, req: Request, table: HeaderMap, ranges: Sequence[dates.DateRange],
                  raw: Mapping[str, Any], errors: Sequence[str]) -> Mapping[str, Any] | None:
    system = prompts.spec_system([c.to_dict() for c in table.columns], [r.to_dict() for r in ranges], None)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": req.text or "Show the table."},
                {"role": "assistant", "content": str(dict(raw))},
                {"role": "user", "content": prompts.repair_message(errors)})
    data = specs.load_answer(await ask_once(deps, messages)) or {}
    fixed = data.get("spec")
    return fixed if isinstance(fixed, Mapping) else None


def _column_pick(req: Request, amb: specs.Ambiguity, raw: Mapping[str, Any], from_model: bool,
                 resume: _Resume) -> TurnResult:
    meta = question_metadata(prompts.COLUMN_PICK_HEADER.format(term=amb.term)[:200],
                             [(c[:200], "") for c in amb.candidates], allow_custom_input=False)
    pause = {"kind": "column_pick", "request": req.to_dict(), "term": amb.term, "candidates": list(amb.candidates),
             "spec": dict(raw), "spec_from_model": from_model, "picks": dict(resume.picks),
             "sheet": resume.sheet.to_dict() if resume.sheet else None, "tab": resume.tab}
    text = f"\"{amb.term}\" matches several columns: {', '.join(amb.candidates)}. Which one did you mean?"
    return _pause(text, pause, meta)


def _verbatim(query: specs.QuerySpec, req: Request, table: HeaderMap,
              ranges: Sequence[dates.DateRange]) -> tuple[specs.QuerySpec, tuple[str, ...]]:
    """Every literal the model put in the spec must be in the user's words, the data, or a resolved range."""
    cells = " ".join(str(c) for r in table.rows for c in r.cells)
    seen = checks.normalise_text(" ".join((req.text, cells, *(f"{r.start} {r.end}" for r in ranges))))
    kept, dropped = [], []
    for f in query.where:
        values = f.value if isinstance(f.value, list) else [f.value]
        if all(checks.appears_in(v, seen) for v in values):
            kept.append(f)
        else:
            dropped.append(f"{f.column} {f.op} {f.value}")
    compare = query.compare
    if compare is not None and not (checks.appears_in(compare.start.isoformat(), seen)
                                    and checks.appears_in(compare.end.isoformat(), seen)):
        dropped.append(f"compare {compare.start}..{compare.end}")
        compare = None
    return query.model_copy(update={"where": kept, "compare": compare}), tuple(dropped)


def _answer(deps: TurnDeps, req: Request, table: HeaderMap, source: models.Source, query: specs.QuerySpec,
            missing: tuple[str, ...], dropped: tuple[str, ...], quality: models.Quality, notes: tuple[str, ...],
            resume: _Resume, ranges: Sequence[dates.DateRange]) -> TurnResult:
    nothing_left = bool(missing) and not (query.select or query.agg or query.group_by or query.compare)
    result = engine.Result((), (), ()) if nothing_left else engine.execute(table, query)
    errors, samples, numeric = models.numeric_checks(table.columns, table.rows)
    quality = replace(quality, picked_column=bool(resume.picks), parse_errors=errors, parse_error_samples=samples,
                      numeric_cells=numeric, dropped=len(dropped), missing=missing,
                      ambiguous_dates=tuple(c.name for c in table.columns if c.ambiguous_dates),
                      mixed_units=models.mixed_units(table.columns, table.rows))
    pull = models.Pull(result, source, deps.now, query.to_dict(), quality, table.names,
                       (*notes, *_notes(table, query, result, quality, dropped, ranges)), render.label(query))
    env = models.envelope(pull, render.summary(pull))
    reply = render.json_block(env) if req.json_mode else render.markdown(pull)
    return TurnResult(reply, None, env, {"spec": query.to_dict()} if not nothing_left else {})


def _notes(table: HeaderMap, query: specs.QuerySpec, result: engine.Result, quality: models.Quality,
           dropped: Sequence[str], ranges: Sequence[dates.DateRange]) -> tuple[str, ...]:
    notes: list[str] = []
    if quality.missing:
        missing = ", ".join(f'"{m}"' for m in quality.missing)
        notes.append(f"No column called {missing} in this table. Columns: {', '.join(table.names)}.")
    if result.windows:
        notes.append(f"Current period {result.windows['current']}, previous {result.windows['previous']}.")
    used = [r for r in ranges if any(isinstance(f.value, list) and r.start.isoformat() in map(str, f.value)
                                     for f in query.where)]
    notes.extend(f"\"{r.phrase}\" = {r.start.isoformat()} to {r.end.isoformat()}." for r in used)
    if result.blank_count:
        notes.append(f"{result.blank_count} empty cell(s) left out of the totals.")
    if quality.parse_errors:
        notes.append(f"{quality.parse_errors} cell(s) in number columns are not numbers: "
                     + "; ".join(quality.parse_error_samples) + ".")
    if quality.ambiguous_dates:
        notes.append(f"Dates in {', '.join(quality.ambiguous_dates)} read as month/day/year (could be either).")
    if quality.mixed_units:
        notes.append(f"Mixed currencies or units in {', '.join(quality.mixed_units)}: totals mix them.")
    if dropped:
        notes.append(f"Left out {len(dropped)} filter(s) the request and the data did not support: "
                     + "; ".join(dropped) + ".")
    if result.skipped_filters:
        notes.append("Skipped filter(s) whose value doesn't fit the column: " + "; ".join(result.skipped_filters) + ".")
    if table.renamed:
        notes.append(f"Renamed duplicate or empty headers: {', '.join(table.renamed)}.")
    return tuple(notes)
