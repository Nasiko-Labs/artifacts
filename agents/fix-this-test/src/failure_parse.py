"""Pure: what a pasted pytest run says. Failing test ids (`path::name[param]`), their exception types,
the `E   ` lines and the `file:line` frames. Computed in code; the model never parses the output."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TARGETS = 3

_SUMMARY = re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+)(?:\s+-\s+(.*))?$", re.MULTILINE)
_HEADER = re.compile(r"^_{3,} (?:ERROR collecting )?(\S+) _{3,}$", re.MULTILINE)
_E_LINE = re.compile(r"^E\s+(.*\S)\s*$", re.MULTILINE)
_EXC = re.compile(r"^([A-Za-z_][\w.]*?(?:Error|Exception|Exit|Warning|Interrupt|Failure|Failed))\b")
_SHORT_FRAME = re.compile(r"^([\w./\\-]+\.py):(\d+):", re.MULTILINE)
_LONG_FRAME = re.compile(r'File "([^"]+\.py)", line (\d+)')
_PY_SIGNAL = re.compile(r"Traceback \(most recent call last\)|test session starts|\bpytest\b|\.py:\d+|"
                        r"AssertionError|^E\s", re.MULTILINE)
_OTHER_SIGNAL = re.compile(r"^--- FAIL: |\.(?:test|spec)\.[jt]sx?\b|^\s*Tests:\s+\d+ failed|\.java:\d+\)|"
                           r"\bjunit\b|\bjest\b|\bmocha\b|\bgo test\b|\bcargo test\b|\brspec\b",
                           re.MULTILINE | re.IGNORECASE)
_MODULE_MISSING = re.compile(r"No module named '([\w.]+)'")
_PY_SOURCE_EXT = re.compile(r"\.(?:js|jsx|ts|tsx|go|java|rb|rs|cs|kt|php)\b")


@dataclass(frozen=True)
class Failure:
    ids: tuple[str, ...] = ()                    # failing test ids, as pasted (at most MAX_TARGETS)
    exceptions: tuple[tuple[str, str], ...] = ()  # (test id or name, exception type)
    e_lines: tuple[str, ...] = ()
    frames: tuple[tuple[str, int], ...] = ()
    more_ids: int = 0                            # failing ids beyond MAX_TARGETS, not targeted

    @property
    def found(self) -> bool:
        return bool(self.ids or self.e_lines or self.frames)

    def exception_for(self, test_id: str) -> str | None:
        name = test_name(test_id)
        for key, exc in self.exceptions:
            if key == test_id or test_name(key) == name:
                return exc
        return self.exceptions[0][1] if len(self.exceptions) == 1 else None


def test_name(test_id: str) -> str:
    """`tests/test_x.py::TestA::test_y[1]` -> `test_y[1]`; a bare name stays as it is."""
    return test_id.rsplit("::", 1)[-1].rsplit(".", 1)[-1]


def exception_of(message: str) -> str | None:
    match = _EXC.match((message or "").strip())
    return match.group(1).rsplit(".", 1)[-1] if match else None


def parse(text: str) -> Failure:
    text = text or ""
    ids: list[str] = []
    exceptions: list[tuple[str, str]] = []
    for match in _SUMMARY.finditer(text):
        test_id = match.group(1)
        if test_id not in ids:
            ids.append(test_id)
        exc = exception_of(match.group(2) or "")
        if exc:
            exceptions.append((test_id, exc))
    if not ids:
        ids = list(dict.fromkeys(m.group(1) for m in _HEADER.finditer(text) if "test" in m.group(1).casefold()))
    e_lines = tuple(m.group(1) for m in _E_LINE.finditer(text))
    if not exceptions:
        first = next((exception_of(line) for line in e_lines if exception_of(line)), None)
        if first and ids:
            exceptions.append((ids[0], first))
    frames = [(m.group(1).replace("\\", "/"), int(m.group(2))) for m in _SHORT_FRAME.finditer(text)]
    frames += [(m.group(1).replace("\\", "/"), int(m.group(2))) for m in _LONG_FRAME.finditer(text)]
    return Failure(tuple(ids[:MAX_TARGETS]), tuple(exceptions), e_lines, tuple(dict.fromkeys(frames)),
                   max(0, len(ids) - MAX_TARGETS))


def other_language(text: str) -> bool:
    """True when the paste is a failing run of another language or runner, with no sign of pytest."""
    text = text or ""
    if _PY_SIGNAL.search(text) and not _PY_SOURCE_EXT.search(text):
        return False
    return bool(_OTHER_SIGNAL.search(text) or _PY_SOURCE_EXT.search(text))


def missing_modules(output: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(1) for m in _MODULE_MISSING.finditer(output or "")))
