"""Turn the user's message into a Run: rows (code), SLO targets (code, then at most one model read when code sees
a target-like phrase it cannot parse), and the one SLO question when a service still has no target or period.
No default target is ever assumed."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import buckets
import prompts
import sli
import slo
import smalltalk
from deps import TurnDeps
from llm import ask_once
from models import Run
from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata
from slo import Slo

SLO_KEY = "slo.question"
LARGE_PASTE_CHARS = 100_000
DEFAULT_PERIOD_DAYS = 30
OPTION_TARGETS = ("99.9", "99.5", "99")
_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Step:
    run: Run | None = None
    reply: str | None = None
    question: dict | None = None


def has_data(text: str) -> bool:
    return sli.parse(text).found


def words_outside_table(text: str, parsed: sli.Parsed) -> str:
    return "\n".join(line for i, line in enumerate(text.splitlines()) if i not in parsed.table_lines)


async def read(deps: TurnDeps, text: str) -> Step:
    parsed = sli.parse(text)
    if not parsed.samples:
        return Step(reply=prompts.NO_ROWS)
    aggregated = len(text) > LARGE_PASTE_CHARS
    samples = buckets.hourly(parsed.samples) if aggregated else parsed.samples
    run = Run(tuple(samples), {}, parsed.rejected, aggregated)
    words = words_outside_table(text, parsed)
    targets = {**_envelope_slos(parsed, run.services()), **slo.find(words, run.services())}
    run = Run(run.samples, targets, run.rejected, run.aggregated)
    if run.missing() and slo.mentions_slo(words):
        model, off_topic = await _model_slos(deps, words, run.missing())
        if off_topic:
            return Step(reply=smalltalk.OFF_TOPIC_REPLY)
        run = Run(run.samples, {**run.slos, **_merged(run.slos, model)}, run.rejected, run.aggregated)
    if run.missing():
        return Step(run=run, question=slo_question(run))
    return Step(run=run)


def _envelope_slos(parsed: sli.Parsed, services: Sequence[str]) -> dict[str, Slo]:
    found = {}
    for item in parsed.slos:
        percent = slo.valid_percent(item.percent)
        if item.service in services and percent:
            found[item.service] = Slo(percent, slo.valid_days(str(item.period_days)) if item.period_days else None)
    return found


def _merged(current: Mapping[str, Slo], new: Mapping[str, Slo]) -> dict[str, Slo]:
    """New targets fill gaps; a period-only completion keeps the user's percent."""
    out = {}
    for name, value in new.items():
        old = current.get(name)
        if old is None or not old.complete and value.period_days is not None and value.percent == old.percent:
            out[name] = value
    return out


async def _model_slos(deps: TurnDeps, words: str, missing: Sequence[str]) -> tuple[dict[str, Slo], bool]:
    system = prompts.INTAKE_SYSTEM.format(services=", ".join(missing))
    answer = await ask_once(deps, ({"role": "system", "content": system},
                                   {"role": "user", "content": prompts.fence_data(words)}))
    match = _JSON.search(answer or "")
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}, False
    if not isinstance(data, Mapping):
        return {}, False
    if data.get("status") == "off_topic":
        return {}, True
    return _verified(data.get("slos"), words, missing), False


def _verified(raw, words: str, missing: Sequence[str]) -> dict[str, Slo]:
    """Only targets whose percent (and period, when given) appear verbatim in the user's words."""
    found = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = next((m for m in missing if m.casefold() == str(item.get("service") or "").casefold()), None)
        percent = slo.valid_percent(str(item.get("slo_percent") or "").rstrip("%"))
        days_text = str(item.get("period_days")) if item.get("period_days") not in (None, "", "null") else None
        if name is None or percent is None or not _verbatim(percent, words):
            continue
        days = slo.valid_days(days_text) if days_text and _verbatim(days_text, words) else None
        found[name] = Slo(percent, days)
    return found


def _verbatim(token: str, words: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(token)}(?![\d])", words) is not None


def apply_answer(run: Run, answer: str) -> Run | None:
    """The run with the answer's targets applied to the services still missing one; None when unreadable."""
    missing = run.missing()
    named = slo.find(answer, missing)
    plain = slo.bare(answer)
    if not named and plain is None:
        return None
    filled = {}
    for name in missing:
        old = run.slos.get(name)
        new = named.get(name) or plain
        if new is None:
            continue
        if not new.complete and old is not None and old.complete:
            continue
        filled[name] = new if new.complete or old is None else Slo(new.percent, old.period_days)
    if not filled:
        return None
    return Run(run.samples, {**run.slos, **filled}, run.rejected, run.aggregated, run.related, run.related_notes)


def slo_question(run: Run) -> dict:
    missing = run.missing()
    header = _header(missing)
    given = sorted({run.slos[m].percent for m in missing if m in run.slos})
    targets = list(dict.fromkeys([*given, *OPTION_TARGETS]))[:4]
    options = [(f"{t}% over {DEFAULT_PERIOD_DAYS} days", "applies to " + ", ".join(missing)) for t in targets]
    hitl = question_metadata(header, options, multi_select=False, allow_custom_input=True)
    text = (f"I need an SLO target and period for {', '.join(missing)} before I can forecast; I never assume one. "
            "Pick one, or type your own (e.g. `payments 99.95% 30d, search 99.5% 28d`).")
    return {"text": text, "hitl": hitl, "metadata": {SLO_KEY: {"services": missing}}}


def _header(missing: Sequence[str]) -> str:
    header = f"SLO target for {', '.join(missing)}?"
    if len(header.encode()) <= MAX_QUESTION_HEADER_LEN:
        return header
    return f"SLO target for {missing[0]} and {len(missing) - 1} more?"[:MAX_QUESTION_HEADER_LEN]
