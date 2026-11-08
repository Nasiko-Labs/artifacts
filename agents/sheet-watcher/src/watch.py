"""The two thinking nodes of the graph: `check` (read the table, resolve the rules, diff against the stored
snapshot, render, and hand back the new snapshot) and `followup` (route a later message in code, else with one
tool-less model call that answers with a sentinel). Both return a partial state update; the graph stores it
only when the node finished, so a failed check never replaces the baseline."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import alert
import events as eventing
import models
import paste
import prompts
import render
import rules as ruling
import sheet
import smalltalk
import snapshot
from deps import TurnDeps
from discovery import discover
from llm import ask_once
from nasiko_hitl import question_metadata
from parse import Request, mentions_domain
from table import MAX_ROWS, Table

log = logging.getLogger("sheet_watcher.watch")

SHEET_KEY = "sw.sheet_pick"   # pause keys: the executor reads a paused task's question by these
KEY_KEY = "sw.key_pick"
POST_HINT = 'Say "post it to #channel" to alert the team; nothing is posted until you approve the exact text.'
_SENTINELS = ("CHECK", "RULES", "POST", "CHANGE_CHANNEL", "RESET_BASELINE", "WATCH_LATER", prompts.OFF_TOPIC)


def _request(state: Mapping[str, Any]) -> Request:
    return Request.from_dict(state.get("request") or {})


def _done(reply: str) -> dict:
    return {"reply": reply, "route": ""}


# --- follow-ups ----------------------------------------------------------------------------------------------

async def followup(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    query, request = state["query"], _request(state)
    if not deps.traceparent:
        return _done(prompts.NO_TRACE_CONTEXT)
    if request.reset:
        return {"route": "check", "reset": True}
    if request.wants_post or request.dry_run or request.change_channel:
        return {"route": "post"}
    parsed = ruling.parse(query, tuple((state.get("snapshot") or {}).get("columns") or ()))
    if parsed.spec != models.RuleSpec() or (parsed.unmatched and not request.check and not request.schedule):
        return {"route": "check", "rules_text": query}
    if request.check:
        return {"route": "check"}
    if request.schedule:
        return _done(prompts.CANNOT_WATCH)
    answer = await ask_once(deps, ({"role": "system", "content": prompts.FOLLOWUP_SYSTEM},
                                   {"role": "user", "content": query}))
    return _sentinel(answer.strip(), query)


def _sentinel(answer: str, query: str) -> dict:
    head = answer.split(":", 1)[0].strip().upper()
    rest = answer.split(":", 1)[1].strip() if ":" in answer else ""
    if head not in _SENTINELS:
        return _done(prompts.UNCLEAR)
    if head == "CHECK":
        return {"route": "check"}
    if head == "RULES":
        return {"route": "check", "rules_text": rest or query}
    if head == "POST":
        return {"route": "post"}
    if head == "CHANGE_CHANNEL":
        return {"route": "post", "request": {**Request().to_dict(), "change_channel": True}}
    if head == "RESET_BASELINE":
        return {"route": "check", "reset": True}
    if head == "WATCH_LATER":
        return _done(prompts.CANNOT_WATCH)
    return _done(smalltalk.OFF_TOPIC_REPLY)


# --- the check -----------------------------------------------------------------------------------------------

async def check(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    query, request = state["query"], _request(state)
    if not deps.traceparent:
        return _done(prompts.NO_TRACE_CONTEXT)
    pasted = paste.read(query)
    if pasted is not None:
        source = {"id": "pasted", "title": "Pasted table", "tab": "", "kind": "pasted"}
        return await _evaluate(state, deps, pasted, source, query, False)
    if request.schedule and not request.sheet and not state.get("source") and not state.get("pick"):
        return _done(prompts.CANNOT_WATCH)
    read = await _read_sheet(state, deps, request)
    if isinstance(read, dict):
        return read
    found, source, seen, partial = read
    return await _evaluate(state, deps, found, source, seen, partial)


async def _read_sheet(state: Mapping[str, Any], deps: TurnDeps, request: Request):
    query = state["query"]
    pick = state.get("pick") or {}
    remembered = state.get("source") or {}
    if remembered.get("kind") != "sheet":
        remembered = {}
    sheet_id, title = pick.get("id"), pick.get("title")
    if not sheet_id and remembered and (not request.sheet or request.sheet.casefold()
                                        in str(remembered.get("title") or "").casefold()):
        sheet_id, title = remembered.get("id"), remembered.get("title")
    if not sheet_id and not request.sheet and not mentions_domain(query) and await _off_topic(deps, query):
        return _done(smalltalk.OFF_TOPIC_REPLY)
    catalog, failure = await discover(deps, ("read",))
    if failure is not None:
        return _done(failure)
    reader = sheet.reader_for(deps, catalog)
    tab = request.tab or (remembered.get("tab") if sheet_id and sheet_id == remembered.get("id") else None)
    outcome = await sheet.read(deps, reader, query, request.sheet, tab, sheet_id, title)
    if outcome.off_topic:
        return _done(smalltalk.OFF_TOPIC_REPLY)
    if outcome.candidates:
        return {"question": _sheet_question(outcome.candidates, query), "reply": "", "route": ""}
    if outcome.table is None:
        return _done(outcome.reply or prompts.SHEET_UNREADABLE)
    source = {"id": outcome.sheet_id, "title": outcome.title, "tab": outcome.tab, "kind": "sheet"}
    return outcome.table, source, f"{reader.fenced.seen_text}\n{query}", outcome.partial


async def _off_topic(deps: TurnDeps, query: str) -> bool:
    answer = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                                   {"role": "user", "content": query}))
    return not answer.strip().upper().startswith(prompts.IN_SCOPE)


def _sheet_question(candidates: tuple[tuple[str, str], ...], query: str) -> dict:
    hitl = question_metadata(prompts.SHEET_HEADER, [(title[:200], "") for _, title in candidates],
                             multi_select=False, allow_custom_input=False)
    names = {title[:200]: cid for cid, title in candidates}
    return {"text": "I found more than one spreadsheet that could be it. " + prompts.SHEET_HEADER, "hitl": hitl,
            "metadata": {SHEET_KEY: {"names": names, "query": query}}}


# --- rules, events, render ---------------------------------------------------------------------------------

async def _rules(state: Mapping[str, Any], deps: TurnDeps, found: Table, same: bool,
                 seen: str) -> tuple[models.RuleSpec, int]:
    base = models.RuleSpec(**state["rules"]) if same and state.get("rules") else models.RuleSpec()
    text = state.get("rules_text") or state["query"]
    parsed = ruling.parse(text, found.header)
    spec, dropped = base, 0
    if parsed.unmatched and deps.traceparent:
        system = prompts.rules_system(list(found.header))
        answer = sheet.load_json_object(await ask_once(deps, ({"role": "system", "content": system},
                                                              {"role": "user", "content": text}))) or {}
        if answer.get("status") == "ok":
            translated, dropped = ruling.validate_model_rules(answer.get("rules"), found.header, f"{text}\n{seen}")
            spec = ruling.merge(spec, translated)
    spec = ruling.merge(spec, parsed.spec)
    if state.get("key_pick"):
        picked = state["key_pick"]
        spec = spec.model_copy(update={"key_column": ruling.ROW_KEY if picked == prompts.ROW_NUMBER else picked})
    return spec, dropped


def _key_question(state: Mapping[str, Any], found: Table, source: Mapping[str, Any]) -> dict:
    options = [*(c[:200] for c in ruling.key_candidates(found)), prompts.ROW_NUMBER]
    hitl = question_metadata(prompts.KEY_HEADER, [(o, "") for o in options], multi_select=False,
                             allow_custom_input=False)
    pick = {"id": source["id"], "title": source["title"]} if source.get("kind") == "sheet" else None
    text = ("No column has a different value in every row, so I can't tell rows apart on my own. "
            + prompts.KEY_HEADER)
    return {"question": {"text": text, "hitl": hitl,
                         "metadata": {KEY_KEY: {"query": state["query"], "pick": pick, "options": options}}},
            "reply": "", "route": ""}


async def _evaluate(state: Mapping[str, Any], deps: TurnDeps, found: Table, source: dict, seen: str,
                    partial: bool) -> dict:
    request = _request(state)
    stored = None if state.get("reset") else state.get("snapshot")
    same = snapshot.same_source(state.get("snapshot"), source)
    prior = stored if snapshot.same_source(stored, source) else None
    spec, dropped = await _rules(state, deps, found, same, seen)
    resolved = ruling.resolve(spec, found, (prior or {}).get("key_column"))
    if resolved.key_column is None and spec.key_column != ruling.ROW_KEY and prior is None:
        return _key_question(state, found, source)
    keyed = snapshot.key_rows(found, resolved)
    previous = snapshot.rows_of(prior) if prior is not None else None
    reported = list(state.get("reported") or ()) if prior is not None else []
    result = eventing.compute(keyed, previous, resolved, deps.now.date(), reported)
    found_events = list(result.events)
    quality = models.Quality(pasted=source["kind"] == "pasted", first_run=previous is None,
                             duplicate_keys=bool(result.duplicates), bad_dates=result.bad_dates > 0,
                             capped=found.capped, row_number_key=resolved.key_column is None, dropped=dropped,
                             partial=partial)
    confidence = models.confidence_of(quality)
    notes = _notes(state, request, found, resolved, result, quality, same)
    title = str(source.get("title") or "spreadsheet")
    since = _when(prior["taken_at"]) if prior else None
    view = render.View(title, tuple(found_events), len(found.rows), previous is None, since,
                       ruling.describe(resolved), notes, (), confidence, POST_HINT)
    env = _envelope(title, source, found_events, confidence, prior, resolved, found, notes)
    reply = render.json_block(env) if request.json_mode else render.markdown(view)
    wants_post = request.wants_post or request.dry_run
    return {"rules": spec.model_dump(), "source": dict(source),
            "snapshot": snapshot.build(keyed, resolved, source, found.header, deps.now),
            "reported": [e.signature for e in found_events], "alert": alert.text(title, found_events),
            "last_envelope": env, "post": {"channel": None, "state": "not_requested", "link": None},
            "reply": reply, "envelope": env, "route": "post" if wants_post else "", "rules_text": "",
            "reset": False}


def _notes(state: Mapping[str, Any], request: Request, found: Table, resolved: ruling.Resolved,
           result: eventing.Result, quality: models.Quality, same: bool) -> tuple[str, ...]:
    notes: list[str] = []
    if request.schedule:
        notes.append(prompts.CANNOT_WATCH)
    if state.get("reset"):
        notes.append(prompts.RESET_NOTE)
    elif state.get("snapshot") and not same:
        notes.append(prompts.NEW_SHEET_NOTE)
    if quality.row_number_key:
        notes.append("Rows are identified by row number, so inserting, deleting or sorting rows will look like "
                     "changes. Say \"key is <column>\" to pick a column.")
    elif "key" in resolved.defaults:
        notes.append(f"I picked {resolved.key_column} as the row key (the first column with a different value in "
                     "every row); say \"key is <column>\" to change it.")
    if found.capped:
        notes.append(f"The sheet has more than {MAX_ROWS:,} rows; I read and compared the first {MAX_ROWS:,}.")
    if quality.partial:
        notes.append("The read stopped before it finished, so this check may be incomplete.")
    if quality.dropped:
        notes.append(f"Left out {quality.dropped} rule value(s) that neither your words nor the sheet support.")
    if found.renamed:
        notes.append(f"Renamed duplicate or empty headers: {', '.join(found.renamed)}.")
    notes.extend(result.notes)
    return tuple(notes)


def _envelope(title: str, source: Mapping[str, Any], found_events: list[models.Event], confidence: str,
              prior: Mapping[str, Any] | None, resolved: ruling.Resolved, found: Table,
              notes: tuple[str, ...]) -> dict:
    counts: dict[str, int] = {"rows": len(found.rows), "events": len(found_events),
                              "new": sum(not e.repeat for e in found_events),
                              "repeat": sum(e.repeat for e in found_events)}
    for e in found_events:
        counts[e.type] = counts.get(e.type, 0) + 1
    baseline = models.Baseline(taken_at=prior["taken_at"], rows=len(prior.get("rows") or {})) if prior else None
    env = models.Envelope(summary=render.summary(title, found_events, prior is None), items=found_events,
                          confidence=confidence, needs_human=models.needs_human_of(found_events),
                          events=found_events, baseline=baseline, first_run=prior is None,
                          rules=resolved.to_dict(), counts=counts, post=models.Post(), coverage=[],
                          notes=list(notes), sheet={"title": title, "tab": source.get("tab") or None,
                                                    "kind": source.get("kind")})
    return env.model_dump()


def _when(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{moment.day} {moment.strftime('%b %H:%M')}"
