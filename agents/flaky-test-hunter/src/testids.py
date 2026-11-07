"""Pure: read the failing test ids out of a CI job log, in code.

The log is cleaned first (ANSI codes, CI timestamp prefixes, group markers); the raw text is kept by the
caller for verbatim checks. Each failure carries a normalised id (`path::name` where the log names the
file), the file when known, the bare test name (what the patch generator looks for) and the cleaned line
it came from. Formats: pytest, JUnit (Maven surefire, Gradle), Jest / Vitest, Go, Rust, RSpec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_RAW_CHARS = 2_000_000

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_GROUP = re.compile(r"^##\[(?:group|endgroup|error)\]")

_JS_EXT = r"(?:js|jsx|ts|tsx|mjs|cjs)"
_PYTEST = re.compile(r"(?:^|\s)FAILED ([\w./-]+\.py::[\w\[\]./:-]+)")
_PYTEST_SHORT = re.compile(r"^([\w./-]+\.py::[\w\[\]./:-]+) FAILED")
_JEST_FILE = re.compile(rf"^\s*FAIL\s+([\w./@-]+\.{_JS_EXT})\s*$")
_VITEST = re.compile(rf"^\s*(?:FAIL|×|✗)\s+([\w./@-]+\.{_JS_EXT})\s+>\s+(.+?)\s*(?:\d+ms)?\s*$")
_JEST_TITLE = re.compile(r"^\s*● (.+? › .+?)\s*$")
_GO = re.compile(r"^\s*--- FAIL: (\S+)")
_RUST = re.compile(r"^test (\S+) \.\.\. FAILED")
_RSPEC = re.compile(r"^rspec \./([\w./-]+\.rb):(\d+) # (.+?)\s*$")
_GRADLE = re.compile(r"^\s*([\w.$]+) > (\w+)(?:\(\))? FAILED")
_SUREFIRE_PAREN = re.compile(r"^\s*(?:\[ERROR\]\s+)?(\w+)\(([\w.$]+)\).*<<< (?:FAILURE|ERROR)!")
_SUREFIRE_DOTTED = re.compile(r"^\s*(?:\[ERROR\]\s+)?([\w.$]+)\.(\w+)\s+--\s+Time elapsed.*<<< (?:FAILURE|ERROR)!")
_SUREFIRE_SUMMARY = re.compile(r"^\s*\[ERROR\]\s+(?:Failures:\s+|Errors:\s+)?([A-Z][\w$]*(?:\.[\w$]+)*)\.(\w+):(\d+)\b")


@dataclass(frozen=True)
class Failure:
    test_id: str
    file: str      # repository-relative test file, when the log names it
    name: str      # the bare test name the patch generator looks for
    line: str      # the cleaned log line it was read from


def clean_line(line: str) -> str:
    return _GROUP.sub("", _TIMESTAMP.sub("", _ANSI.sub("", line))).rstrip()


def clean(raw: str) -> list[str]:
    return [clean_line(line) for line in (raw or "")[-MAX_RAW_CHARS:].splitlines()]


def failures(raw: str) -> tuple[Failure, ...]:
    """Every failing test in the log, once each, in log order."""
    found: dict[str, Failure] = {}
    js_file = ""
    for line in clean(raw):
        m = _JEST_FILE.match(line)
        if m:
            js_file = m.group(1)
            continue
        failure = _read(line, js_file)
        if failure is not None:
            found.setdefault(failure.test_id, failure)
    return tuple(found.values())


def _read(line: str, js_file: str) -> Failure | None:
    for reader in (_pytest, _js, _go, _rust, _rspec, _junit):
        failure = reader(line, js_file)
        if failure is not None:
            return failure
    return None


def _pytest(line: str, _: str) -> Failure | None:
    m = _PYTEST.search(line) or _PYTEST_SHORT.match(line)
    if not m:
        return None
    test_id = m.group(1)
    return Failure(test_id, test_id.split("::", 1)[0], pytest_name(test_id), line.strip())


def pytest_name(test_id: str) -> str:
    """`tests/a.py::TestX::test_y[1-2]` → `test_y`."""
    return re.sub(r"\[.*\]$", "", test_id.rsplit("::", 1)[-1])


def _js(line: str, js_file: str) -> Failure | None:
    m = _VITEST.match(line)
    if m:
        title = m.group(2).replace(" > ", " › ")
        return Failure(f"{m.group(1)}::{title}", m.group(1), title.rsplit(" › ", 1)[-1], line.strip())
    m = _JEST_TITLE.match(line)
    if not m:
        return None
    title = m.group(1)
    test_id = f"{js_file}::{title}" if js_file else title
    return Failure(test_id, js_file, title.rsplit(" › ", 1)[-1], line.strip())


def _go(line: str, _: str) -> Failure | None:
    m = _GO.match(line)
    if not m:
        return None
    return Failure(m.group(1), "", m.group(1).split("/", 1)[0], line.strip())


def _rust(line: str, _: str) -> Failure | None:
    m = _RUST.match(line)
    if not m:
        return None
    return Failure(m.group(1), "", m.group(1).rsplit("::", 1)[-1], line.strip())


def _rspec(line: str, _: str) -> Failure | None:
    m = _RSPEC.match(line)
    if not m:
        return None
    return Failure(f"{m.group(1)}::{m.group(3)}", m.group(1), m.group(3), line.strip())


def _junit(line: str, _: str) -> Failure | None:
    m = _GRADLE.match(line) or _SUREFIRE_DOTTED.match(line) or _SUREFIRE_SUMMARY.match(line)
    if m:
        cls, method = m.group(1), m.group(2)
    else:
        m = _SUREFIRE_PAREN.match(line)
        if not m:
            return None
        method, cls = m.group(1), m.group(2)
    return Failure(f"{cls}::{method}", "", method, line.strip())


def verbatim_line(failure: Failure, raw: str) -> str:
    """The failure's line when it appears exactly in the raw log, else "" (never quote what isn't there)."""
    return failure.line if failure.line and failure.line in (raw or "") else ""
