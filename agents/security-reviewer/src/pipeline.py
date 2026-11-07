"""One review: mask -> skip/manifest split -> batch -> scan (rules) -> triage + sweep (<= 5 calls, <= 2
repairs) -> merge -> dedupe. Secret originals exist only inside `review()` and are never returned."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import batching
import deps as deps_mod
import merge
from diff_parse import CodeFile, Line, Parsed
from envelope import Coverage, Review
from grounding import corpus
from langs import language, skip_reason
from llm import TurnDeps
from rules import scan_file
from secret_scan import Masker, unmask_display
from triage import FAILED, OFF_TOPIC, Batch, run_batches

FOCUS_FAMILIES = {"injection": {"injection", "ssrf", "path-traversal", "deserialization", "parsing"},
                  "auth": {"auth", "authz-missing", "idor", "auth-bypass", "mass-assignment", "secrets"}}
_SHOWN = ("issue", "impact", "fix", "fix_code", "evidence")


@dataclass(frozen=True)
class Outcome:
    status: str                                   # ok | off_topic | no_traceparent
    review: Review | None = None
    files: tuple[CodeFile, ...] = ()              # masked
    displays: dict = field(default_factory=dict)  # placeholder -> "AKIA…"
    context: tuple[str, ...] = ()


def mask_files(masker: Masker, files: tuple[CodeFile, ...]) -> tuple[tuple[CodeFile, ...], list[dict]]:
    masked_files, secrets, seen = [], [], set()
    for code in files:
        masker.in_key = False
        lines = []
        for ln in code.lines:
            text, hits = masker.mask_line(ln.text)
            lines.append(Line(ln.no, text, ln.kind))
            for placeholder in hits:
                if placeholder not in seen:
                    seen.add(placeholder)
                    secrets.append(merge.from_secret(placeholder, masker.labels[placeholder], masker.displays[placeholder],
                                                     code.path, ln.no, text.strip()[:300]))
        masked_files.append(CodeFile(code.path, tuple(lines), code.is_diff))
    return tuple(masked_files), secrets


async def review(deps: TurnDeps, parsed: Parsed, policy: tuple[str, ...] = (), focus: str = "") -> Outcome:
    masker = Masker()
    files, secret_findings = mask_files(masker, parsed.files)
    instruction, hits = masker.mask_text(parsed.instruction)
    context = tuple(masker.mask_text(c)[0] for c in parsed.context)
    secret_findings += [merge.from_secret(p, masker.labels[p], masker.displays[p], "your message", 0, "")
                        for p in dict.fromkeys(hits) if p not in {s["placeholder"] for s in secret_findings}]
    skipped, manifests, code_files, dep_findings = _split(files, focus)
    langs = {f.path: language(f) for f in code_files}
    planned, size_skipped = batching.plan(code_files)
    batches = _scan(planned, langs)
    norm = corpus(files, (instruction, *context))
    results = []
    if batches:
        if not deps.traceparent:
            return Outcome("no_traceparent")
        results = await run_batches(deps, batches, instruction, context, policy, norm)
    answered = [r for r in results if r.status != FAILED]
    if answered and sum(r.status == OFF_TOPIC for r in answered) * 2 > len(answered):
        return Outcome(OFF_TOPIC)
    dropped = sum((r.dropped for r in results), Counter())
    found = [*secret_findings, *_triaged(batches, results, norm, dropped), *dep_findings]
    found += [merge.from_extra(e) for r in results for e in r.extras]
    found = [f for f in found if _in_focus(f, focus)]
    shown = [{**f, **{k: unmask_display(f[k], masker.displays) if f.get(k) else f.get(k) for k in _SHOWN}}
             for f in merge.finalize(found, dropped)]
    coverage = Coverage(tuple(dict.fromkeys(f.path for b in planned for f in b)), tuple(skipped + size_skipped),
                        tuple(manifests), tuple(p for p, lang in langs.items() if lang == "none"), len(batches),
                        sum(r.status == FAILED for r in results), any(r.repaired for r in results), focus)
    return Outcome("ok", Review(tuple(shown), coverage, len(masker.displays), dropped), files, dict(masker.displays),
                   context)


def _split(files: tuple[CodeFile, ...], focus: str):
    skipped, manifests, code_files, dep_findings = [], [], [], []
    for code in files:
        reason = skip_reason(code)
        eco = None if reason else deps_mod.ecosystem(code)
        if reason:
            skipped.append({"file": code.path, "reason": reason, "lines": "all"})
        elif eco:
            manifests.append(code.path)
            dep_findings += deps_mod.check(code, eco)
        else:
            code_files.append(code)
    if focus in ("secrets", "dependencies"):
        code_files = []
    if focus == "secrets":
        dep_findings = []
    return skipped, manifests, code_files, dep_findings


def _scan(planned: list[tuple[CodeFile, ...]], langs: dict[str, str]) -> list[Batch]:
    batches, n = [], 1
    for files in planned:
        found = []
        for code in files:
            found += scan_file(code, langs.get(code.path, "generic"), start=n + len(found))
        n += len(found)
        batches.append(Batch(files, tuple(found)))
    return batches


def _triaged(batches: list[Batch], results: list, norm: str, dropped: Counter) -> list[dict]:
    out = []
    for batch, result in zip(batches, results or [None] * len(batches)):
        triage = result.triage if result is not None else {}
        for c in batch.candidates:
            finding = merge.from_candidate(c, triage.get(c.id), norm, dropped)
            if finding is not None:
                out.append(finding)
    return out


def _in_focus(finding: dict, focus: str) -> bool:
    families = FOCUS_FAMILIES.get(focus)
    return families is None or finding["family"] in families or finding["kind"] == "secret"
