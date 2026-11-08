"""Whether the fetched files can be tested, the fixed command, and the parse of a run into the `tests`
record. Only Python/pytest runs in v1 (the image has no other toolchain); everything else is `not_run`
with the reason. The PR body's "How tested" line is built from this record, never from the model."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import paths as p
import sandbox

COMMAND = ("-q", "-rA", "-p", "no:cacheprovider", "--no-header", "--tb=short", "--rootdir=.")
_FINAL = re.compile(r"^=*\s*((?:\d+ \w+,?\s*)+)in [\d.]+s\b.*$", re.MULTILINE)
_PAIR = re.compile(r"(\d+) (\w+)")
_MISSING_DEP = re.compile(r"ModuleNotFoundError|ImportError|No module named")
_PY_CONFIG = ("pyproject.toml", "pytest.ini", "setup.cfg")


@dataclass(frozen=True)
class Tests:
    ran: bool
    result: str               # passed | failed | not_run
    command: str = ""
    summary: str = ""
    reason: str = ""
    pre_existing_failures: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", None) or k in ("ran", "result")}

    @staticmethod
    def from_dict(data: Mapping | None) -> Tests:
        if not data:
            return NOT_RUN
        return Tests(bool(data.get("ran")), str(data.get("result") or "not_run"), str(data.get("command") or ""),
                     str(data.get("summary") or ""), str(data.get("reason") or ""),
                     bool(data.get("pre_existing_failures")))

    def line(self) -> str:
        """The exact "How tested" line."""
        if self.result == "passed":
            return f"Ran `{self.command}` in a sandbox: {self.summary}."
        if self.result == "failed":
            extra = " (some failures were already there before the change)" if self.pre_existing_failures else ""
            return f"Ran `{self.command}` in a sandbox: {self.summary}{extra}."
        return f"Not run: {self.reason or 'no runnable tests'}."


NOT_RUN = Tests(False, "not_run", reason="no runnable tests were found among the fetched files")


def test_files(files: Sequence[str]) -> list[str]:
    names = [(f, f.rsplit("/", 1)[-1]) for f in files]
    return [f for f, name in names if p.is_test_file(f) and name.endswith(".py")
            and (name.startswith("test_") or name.endswith("_test.py"))]


def runnable(files: Sequence[str]) -> tuple[list[str], str]:
    """(test files to run, reason when none)."""
    if not any(f.endswith(".py") for f in files):
        return [], "only Python tests run here (no Python files were fetched)"
    tests = test_files(files)
    if not tests:
        return [], "no Python test file was fetched"
    return tests, ""


def command_text(tests: Sequence[str]) -> str:
    return "python -m pytest -q " + " ".join(tests)


@dataclass(frozen=True)
class Counts:
    passed: int = 0
    failed: int = 0
    errors: int = 0


def parse(output: str) -> Counts:
    finals = list(_FINAL.finditer(output or ""))
    if not finals:
        return Counts()
    found = {"passed": 0, "failed": 0, "errors": 0}
    for number, word in _PAIR.findall(finals[-1].group(1)):
        key = {"passed": "passed", "failed": "failed", "error": "errors", "errors": "errors"}.get(word)
        if key:
            found[key] = int(number)
    return Counts(found["passed"], found["failed"], found["errors"])


def summarise(counts: Counts) -> str:
    parts = [f"{counts.passed} passed"] if counts.passed or not (counts.failed or counts.errors) else []
    parts += [f"{counts.failed} failed"] if counts.failed else []
    parts += [f"{counts.errors} errors"] if counts.errors else []
    return ", ".join(parts)


Runner = Callable[[Path, Path, list[str]], sandbox.RunOutput]


class TestRunner:
    """Runs the fixed command; `run` is injectable so the flow tests stay fast."""

    def __init__(self, run: Runner = sandbox.run_pytest):
        self._run = run

    async def execute(self, root: Path, work: Path, tests: Sequence[str]) -> tuple[Tests, Counts]:
        out = await asyncio.to_thread(self._run, root, work, [*COMMAND, *tests])
        command = command_text(tests)
        if out.timed_out:
            return Tests(False, "not_run", command, reason="the test run took over 60 s"), Counts()
        counts = parse(out.output)
        total = counts.passed + counts.failed + counts.errors
        if total == 0 or (counts.errors and not counts.passed and _MISSING_DEP.search(out.output)):
            return Tests(False, "not_run", command, reason="dependencies are not installed here (or a module "
                                                           "the tests import was not fetched)"), counts
        result = "passed" if counts.failed == 0 and counts.errors == 0 else "failed"
        return Tests(True, result, command, summarise(counts)), counts


def with_baseline(after: Tests, baseline: Counts | None) -> Tests:
    pre = baseline is not None and (baseline.failed + baseline.errors) > 0
    return Tests(after.ran, after.result, after.command, after.summary, after.reason, pre)
