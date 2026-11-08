"""Pure: the fixed pytest command built from a scope (the model never passes arguments, paths or shell
syntax) and the parse of a run: counts from pytest's final line (fallback: the -rA outcome lines), each
test's outcome and exception type, and whether collection failed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from failure_parse import exception_of, test_name

SCOPES = ("failing", "file", "all")
BASE_ARGS = ("-q", "-rA", "-p", "no:cacheprovider", "--no-header", "--tb=short", "--rootdir=.")

_OUTCOME = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS)\s+(\S+)(?:\s+-\s+(.*))?$", re.MULTILINE)
_FINAL = re.compile(r"^=*\s*((?:\d+ \w+,?\s*)+)in [\d.]+s\b.*$", re.MULTILINE)
_PAIR = re.compile(r"(\d+) (\w+)")
_NO_TESTS = re.compile(r"^=*\s*no tests ran\b", re.MULTILINE)


@dataclass(frozen=True)
class Results:
    failed: int = 0
    passed: int = 0
    errors: int = 0
    outcomes: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # id -> (status, exception)
    output: str = ""
    timed_out: bool = False

    def counts(self) -> dict:
        return {"failed": self.failed, "passed": self.passed, "errors": self.errors}

    @property
    def total(self) -> int:
        return self.failed + self.passed + self.errors

    @property
    def collection_error(self) -> bool:
        return any(status == "ERROR" and "::" not in tid for tid, (status, _) in self.outcomes.items())

    def find(self, wanted: str) -> str | None:
        """The run's id for a pasted test id (same id, or same file name and test name)."""
        if wanted in self.outcomes:
            return wanted
        name, file = test_name(wanted), wanted.split("::", 1)[0].rsplit("/", 1)[-1] if "::" in wanted else None
        for tid in self.outcomes:
            if test_name(tid) == name and (file is None or tid.split("::", 1)[0].rsplit("/", 1)[-1] == file):
                return tid
        return None

    def status(self, wanted: str) -> str | None:
        tid = self.find(wanted)
        return self.outcomes[tid][0] if tid else None

    def ids_with(self, status: str) -> tuple[str, ...]:
        return tuple(tid for tid, (s, _) in self.outcomes.items() if s == status and "::" in tid)


def command(scope: str, targets: tuple[str, ...], test_files: tuple[str, ...]) -> list[str]:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r}")
    args = list(BASE_ARGS)
    with_path = tuple(t for t in targets if "::" in t)
    if scope == "failing" and with_path:
        return [*args, "-x", *with_path]
    if scope == "failing" and targets:
        names = " or ".join(re.sub(r"\[.*\]$", "", test_name(t)) for t in targets)
        return [*args, "-x", "-k", names, *test_files]
    if scope == "file" and with_path:
        return [*args, *dict.fromkeys(t.split("::", 1)[0] for t in with_path)]
    return [*args, *test_files]


def parse(output: str, timed_out: bool = False) -> Results:
    outcomes: dict[str, tuple[str, str | None]] = {}
    for match in _OUTCOME.finditer(output or ""):
        outcomes[match.group(2)] = (match.group(1), exception_of(match.group(3) or ""))
    counts = {"failed": 0, "passed": 0, "errors": 0}
    finals = list(_FINAL.finditer(output or ""))
    if finals:
        for number, word in _PAIR.findall(finals[-1].group(1)):
            key = {"failed": "failed", "passed": "passed", "error": "errors", "errors": "errors"}.get(word)
            if key:
                counts[key] = int(number)
    else:
        statuses = [s for s, _ in outcomes.values()]
        counts = {"failed": statuses.count("FAILED"), "passed": statuses.count("PASSED"),
                  "errors": statuses.count("ERROR")}
    return Results(counts["failed"], counts["passed"], counts["errors"], outcomes, output or "", timed_out)
