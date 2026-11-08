"""From changed files to a gate report, in code: decide what is scanned (language, caps, readable), run
the scanner and the secret scan, keep only findings inside the scanned files and lines, classify each as
new / existing / unknown_diff, de-duplicate, order, and compute the verdict. Explanations are added later
by explain.py; nothing here calls the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import diffmap
import gate
import paths
import scanner
import secret_scan
from records import ChangedFile, Finding, GateReport, PullRequest

MAX_CODE_FILES = 30
SNIPPET_MAX_LINES = 5


@dataclass(frozen=True)
class Target:
    path: str
    content: str
    changed: frozenset[int] | None      # None: no patch, every finding is unknown_diff
    added: tuple[tuple[int, str], ...] = ()
    fragment: bool = False


@dataclass(frozen=True)
class Plan:
    """What will be scanned, and what was not (with the reason)."""

    targets: tuple[Target, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    code_files: int = 0
    skipped_code: int = 0


def code_paths(files: Sequence[ChangedFile], language: str = "") -> tuple[str, ...]:
    """Scannable changed files (existing after the PR, in a scanned language), at most MAX_CODE_FILES."""
    keep = [f.path for f in files if f.status not in ("removed", "binary") and paths.language(f.path)
            and (not language or paths.language(f.path).casefold() == language)]
    return tuple(keep[:MAX_CODE_FILES])


def plan(files: Sequence[ChangedFile], contents: Mapping[str, str], fetch_skipped: Sequence[tuple[str, str]] = (),
         language: str = "") -> Plan:
    skipped: list[tuple[str, str]] = list(fetch_skipped)
    targets: list[Target] = []
    notes: list[str] = []
    allowed = set(code_paths(files, language))
    unreadable = {p for p, _ in fetch_skipped}
    code = [f for f in files if f.status not in ("removed", "binary") and paths.language(f.path)
            and (not language or paths.language(f.path).casefold() == language)]
    for f in files:
        reason = _not_code_reason(f, language)
        if reason:
            skipped.append((f.path, reason))
    for f in code:
        if f.path not in allowed:
            skipped.append((f.path, f"over the {MAX_CODE_FILES}-file cap"))
        elif f.path in unreadable:
            continue
        elif f.path in contents:
            changed = diffmap.changed_lines(f.patch) if f.patch else None
            targets.append(Target(f.path, contents[f.path], changed, diffmap.added_lines(f.patch)))
        elif f.patch.strip():
            targets.append(Target(f.path, diffmap.fragment(f.patch), diffmap.changed_lines(f.patch),
                                  diffmap.added_lines(f.patch), fragment=True))
        else:
            skipped.append((f.path, "contents not readable and no patch"))
    if any(paths.is_dependency_file(f.path) for f in files):
        notes.append("Dependency changes were not scanned (no vulnerability database is available to this agent).")
    if any(t.fragment for t in targets):
        notes.append("Some file contents could not be read, so only their changed hunks were scanned.")
    scanned = {t.path for t in targets}
    skipped_code = sum(1 for f in code if f.path not in scanned)
    return Plan(tuple(targets), tuple(skipped), tuple(notes), len(code), skipped_code)


def _not_code_reason(f: ChangedFile, language: str) -> str:
    if f.status == "removed":
        return "deleted"
    if paths.is_dependency_file(f.path):
        return "dependency manifest or lockfile"
    if f.status == "binary":
        return "binary"
    lang = paths.language(f.path)
    if not lang:
        return "language not scanned"
    if language and lang.casefold() != language:
        return "outside the language you asked for"
    return ""


@dataclass(frozen=True)
class Scanned:
    findings: tuple[Finding, ...]
    status: str
    rejected: tuple[tuple[str, str], ...] = ()
    dropped: int = 0
    model_snippets: Mapping[str, str] = field(default_factory=dict)   # finding id → masked context


async def scan(p: Plan, runner: scanner.Runner | None = None) -> Scanned:
    files = {t.path: t.content for t in p.targets}
    result = await scanner.scan(files, runner=runner)
    by_path = {t.path: t for t in p.targets}
    secrets = [f for t in p.targets for f in secret_scan.scan_added(t.path, t.added)]
    raw = [*result.findings, *secrets]
    found = [gate.classify(f, by_path[f.file].changed) for f in raw if f.file in by_path]
    found = _drop_secret_duplicates(found)
    ordered = gate.ordered(gate.dedupe(found))
    with_snippets = tuple(f.with_(snippet=_display(by_path[f.file].content, f)) for f in ordered)
    context = {f.id: _model_context(by_path[f.file].content, f) for f in ordered}
    return Scanned(with_snippets, result.status, result.rejected, result.dropped, context)


def _drop_secret_duplicates(found: list[Finding]) -> list[Finding]:
    """The in-code secret scan and the bundled secret rule can hit the same line: keep one."""
    secret_lines = {(f.file, f.start_line) for f in found if f.source == "secret scan"}
    return [f for f in found if not (f.is_secret and f.source != "secret scan" and (f.file, f.start_line) in secret_lines)]


def _lines(content: str, start: int, end: int) -> list[tuple[int, str]]:
    rows = content.splitlines()
    lo, hi = max(start, 1), min(end, len(rows))
    return [(n, rows[n - 1]) for n in range(lo, hi + 1)]


def _display(content: str, f: Finding) -> str:
    end = min(f.end_line, f.start_line + SNIPPET_MAX_LINES - 1)
    return "\n".join(secret_scan.mask_display(t.rstrip()) for _, t in _lines(content, f.start_line, end))


def _model_context(content: str, f: Finding) -> str:
    rows = _lines(content, f.start_line - 3, f.end_line + 3)
    return "\n".join(f"{n:>5} {secret_scan.mask_model(t.rstrip())}" for n, t in rows)


def report(p: Plan, s: Scanned, pr: PullRequest | None, mode: str, strict: bool, extra_notes: Sequence[str] = ()) -> GateReport:
    skipped = (*p.skipped, *s.rejected)
    status = s.status if s.status != "empty" else "ok"
    notes = [*p.notes, *extra_notes]
    if s.status == "missing":
        notes.append("The scanner is not available, so only committed secrets on added lines were checked.")
    if s.status == "timeout":
        notes.append("The scan ran out of time; results are partial.")
    if s.status == "failed":
        notes.append("The scanner failed; results are partial.")
    if p.code_files == 0:
        notes.append("No scannable files: only Python, JavaScript/TypeScript, Go and Java are scanned.")
    base = GateReport(verdict="pass", findings=s.findings, pr=pr, scanned=tuple(t.path for t in p.targets),
                      skipped=tuple(skipped), notes=tuple(notes), mode=mode, strict=strict, scanner_status=status,
                      fragments=any(t.fragment for t in p.targets), code_files=p.code_files,
                      skipped_code=p.skipped_code + len(s.rejected))
    return gate.finish(base)
