"""One gate turn end to end. A pull request: discover → pr → files → scan → map → explain → verdict. Pasted
input: a diff or code is scanned the same way (no gateway call); pasted findings are triaged (no scan).
Returns a reply that ends the turn, or a GateReport."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import assemble
import diffmap
import explain
import findings_parse
import gate
import prompts
import request
import secret_scan
import steps
from deps import TurnDeps
from discovery import Discoverer
from reader import TurnReader
from records import ChangedFile, GateReport, PullRequest
from request import Asked
from scanner import Runner

log = logging.getLogger("security_gate.pipeline")

MAX_PASTED_CHARS = 100_000


@dataclass(frozen=True)
class Result:
    reply: str = ""
    report: GateReport | None = None


async def run_pr(deps: TurnDeps, asked: Asked, runner: Runner | None = None) -> Result:
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Result(failure)
    reader = TurnReader(deps, discoverer)
    found = await steps.find_pr(reader, asked.repo, asked.number)
    if found.pr is None:
        return Result(found.reply)
    wanted = assemble.code_paths(found.files, asked.language)
    contents = await steps.fetch_contents(reader, found.pr, wanted)
    p = assemble.plan(found.files, contents.by_path, contents.skipped, asked.language)
    notes = (*found.notes, *contents.notes)
    if not found.files:
        notes = (*notes, "No changed files were returned for this pull request.")
    return await _finish(deps, p, found.pr, "pr", asked.strict, notes, runner)


async def run_pasted(deps: TurnDeps, text: str, asked: Asked, runner: Runner | None = None) -> Result:
    if not deps.traceparent:
        return Result(prompts.NO_TRACE_CONTEXT)
    notes: tuple[str, ...] = ()
    if len(text) > MAX_PASTED_CHARS:
        notes = (f"Only the first {MAX_PASTED_CHARS:,} pasted characters were read.",)
        text = text[:MAX_PASTED_CHARS]
    if asked.pasted == "findings":
        return await _triage(deps, text, notes)
    if asked.pasted == "diff":
        files = diffmap.parse_unified(text[text.find("diff --git ") if "diff --git " in text else 0:])
        p = assemble.plan(files, {}, (), asked.language)
        p = replace(p, targets=tuple(replace(t, fragment=False) for t in p.targets),
                    notes=tuple(n for n in p.notes if not n.startswith("Some file contents")))
        return await _finish(deps, p, None, "diff", asked.strict,
                             (*notes, "Only the pasted hunks were scanned (no whole files)."), runner)
    code = request.code_files(text)
    files = tuple(ChangedFile(path, "added") for path in code)
    p = assemble.plan(files, code, (), asked.language)
    p = replace(p, targets=tuple(_every_line_changed(t) for t in p.targets))
    return await _finish(deps, p, None, "code", asked.strict,
                         (*notes, "Pasted code has no diff, so every line counts as changed."), runner)


async def _finish(deps: TurnDeps, p: assemble.Plan, pr: PullRequest | None, mode: str, strict: bool,
                  notes: tuple[str, ...], runner: Runner | None) -> Result:
    scanned = await assemble.scan(p, runner)
    report = assemble.report(p, scanned, pr, mode, strict, notes)
    explained, missing = await explain.explain(deps, report.findings, scanned.model_snippets)
    extra = ((f"{missing} finding{'s' if missing != 1 else ''} without an explanation (limits or an invalid "
              "answer); the finding itself stands."),) if missing else ()
    log.info("gate %s: %s, %d findings, scanner %s", pr.label if pr else mode, report.verdict, len(report.findings),
             scanned.status)
    return Result(report=replace(report, findings=explained, notes=(*report.notes, *extra)))


async def _triage(deps: TurnDeps, text: str, notes: tuple[str, ...]) -> Result:
    data = findings_parse.pasted_object(text)
    if data is None:
        return Result(prompts.NEED_INPUT)
    found, dropped, unknown = findings_parse.from_pasted(data)
    extra = list(notes)
    if dropped:
        extra.append(f"{dropped} pasted entr{'ies' if dropped != 1 else 'y'} had no file or rule and were skipped.")
    if unknown:
        extra.append(f"{unknown} finding{'s' if unknown != 1 else ''} had an unknown severity, counted as medium.")
    raw = gate.ordered(gate.dedupe(found))
    ordered = tuple(f.with_(snippet=secret_scan.mask_display(f.snippet), message=secret_scan.mask_display(f.message),
                            is_secret=f.is_secret
                            or secret_scan.has_secret(f.snippet)) for f in raw)
    base = GateReport(verdict="pass", findings=ordered, pr=PullRequest.from_dict(data.get("pr")), notes=tuple(extra),
                      mode="findings", scanner_status="not_run")
    report = gate.finish(base)
    masked = {f.id: secret_scan.mask_model(f"{f.start_line:>5} {f.snippet}") if f.snippet else "" for f in raw}
    explained, missing = await explain.explain(deps, report.findings, masked)
    if missing:
        extra.append(f"{missing} finding{'s' if missing != 1 else ''} without an explanation; the finding stands.")
    return Result(report=replace(report, findings=explained, notes=tuple(extra)))


def _every_line_changed(t: assemble.Target) -> assemble.Target:
    rows = tuple(enumerate(t.content.splitlines(), 1))
    return replace(t, changed=frozenset(n for n, _ in rows), added=rows)
