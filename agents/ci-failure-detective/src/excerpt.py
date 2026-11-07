"""Pure: turn a CI job log into what the model may see, and pull the facts out of it in code.

- `clean` strips ANSI codes, CI timestamp prefixes and group markers (the raw text is kept elsewhere,
  untouched, for verbatim checks).
- `excerpt` keeps windows around anchor lines (−15/+10, merged, at most 6, the last ones first) plus
  the log's last 40 lines, capped at 6,000 characters.
- `test_ids` and `locations` read failing tests and `path:line` failure locations for pytest, Jest,
  Go, JUnit, Rust, and compiler errors, keeping only repository-relative paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = 6_000
MAX_RAW_CHARS = 2_000_000
BEFORE, AFTER = 15, 10
MAX_WINDOWS = 6
TAIL_LINES = 40
GAP = "…"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_GROUP = re.compile(r"^##\[(?:group|endgroup)\]")
ANCHOR = re.compile(
    r"FAILED|FAIL:|Error:|error\[|✕|×|panicked at|Traceback|Process completed with exit code [1-9]|"
    r"--- FAIL|AssertionError|error TS\d+|No space left on device|exit code 137|timed out|"
    r"exceeded the maximum execution time|<<< (?:FAILURE|ERROR)!|●")

_TEST_ID_PATTERNS = (
    re.compile(r"FAILED ([\w./-]+\.py::[\w\[\]./:-]+)"),
    re.compile(r"^([\w./-]+\.py::[\w\[\]./:-]+) FAILED"),
    re.compile(r"^\s*● (.+? › .+?)\s*$"),
    re.compile(r"--- FAIL: (\S+)"),
    re.compile(r"^test (\S+) \.\.\. FAILED"),
    re.compile(r"^\s*([\w.$]+) > (\w+)(?:\(\))? FAILED"),
    re.compile(r"^\s*(?:\[ERROR\]\s+)?(\w+)\(([\w.$]+)\)\s+Time elapsed.*<<< (?:FAILURE|ERROR)!"),
    re.compile(r"^\s*(?:\[ERROR\]\s+)?([\w.$]+)\.(\w+)\s+--\s+Time elapsed.*<<< (?:FAILURE|ERROR)!"),
)
_SOURCE_EXT = r"(?:py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|rb|c|cc|cpp|h|hpp|cs|php|swift|scala)"
_LOCATION_PATTERNS = (
    re.compile(rf'File "([^"]+\.{_SOURCE_EXT})", line (\d+)'),
    re.compile(rf"([\w./@+-]+\.{_SOURCE_EXT})\((\d+),\d+\)"),
    re.compile(rf"([\w./@+-]+\.{_SOURCE_EXT}):(\d+)(?::\d+)?"),
)
_WORKSPACE = re.compile(r"^(?:/home/runner/work/[^/]+/[^/]+/|/__w/[^/]+/[^/]+/|/[a-z]+/workspace/|/builds/[^/]+/[^/]+/|"
                        r"/workspace/|/app/|/src/|\./)")
_FOREIGN = ("site-packages/", "dist-packages/", "node_modules/", "/usr/", "<frozen", "go/pkg/mod/", ".cargo/registry",
            "/rustc/", "/opt/", "/lib/python", "internal/")


@dataclass(frozen=True)
class Location:
    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


def clean_line(line: str) -> str:
    return _GROUP.sub("", _TIMESTAMP.sub("", _ANSI.sub("", line))).rstrip()


def clean(raw: str) -> list[str]:
    return [clean_line(line) for line in (raw or "")[-MAX_RAW_CHARS:].splitlines()]


def anchors(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if ANCHOR.search(line)]


def excerpt(raw: str, max_chars: int = MAX_CHARS) -> str:
    """Anchor windows plus the tail, in log order, at most `max_chars` (earliest windows dropped first)."""
    lines = clean(raw)
    if not lines:
        return ""
    spans = _windows(lines, anchors(lines))
    spans = _merge([*spans, (max(0, len(lines) - TAIL_LINES), len(lines))])
    chunks = ["\n".join(lines[a:b]) for a, b in spans]
    while len(chunks) > 1 and len(_joined(chunks)) > max_chars:
        chunks = chunks[1:]
    text = _joined(chunks)
    return text if len(text) <= max_chars else GAP + text[-(max_chars - 1):]


def _windows(lines: list[str], hits: list[int]) -> list[tuple[int, int]]:
    merged = _merge([(max(0, i - BEFORE), min(len(lines), i + AFTER + 1)) for i in hits])
    return merged[-MAX_WINDOWS:]


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _joined(chunks: list[str]) -> str:
    return f"\n{GAP}\n".join(chunks)


def first_anchor_line(raw: str) -> str:
    """The first anchor line of the raw log, verbatim (for a log quote the model got wrong)."""
    for line in (raw or "").splitlines():
        if ANCHOR.search(clean_line(line)):
            return line.strip()
    return ""


def test_ids(raw: str) -> tuple[str, ...]:
    found: list[str] = []
    for line in clean(raw):
        for pattern in _TEST_ID_PATTERNS:
            m = pattern.search(line)
            if m:
                found.append(_test_id(pattern, m))
                break
    return tuple(dict.fromkeys(t for t in found if t))


def _test_id(pattern: re.Pattern, m: re.Match) -> str:
    if pattern is _TEST_ID_PATTERNS[5] or pattern is _TEST_ID_PATTERNS[7]:
        return f"{m.group(1)} > {m.group(2)}"
    if pattern is _TEST_ID_PATTERNS[6]:
        return f"{m.group(2)} > {m.group(1)}"
    return m.group(1).strip()


def locations(raw: str) -> tuple[Location, ...]:
    found: list[Location] = []
    for line in clean(raw):
        for pattern in _LOCATION_PATTERNS:
            for m in pattern.finditer(line):
                path = repo_relative(m.group(1))
                if path:
                    found.append(Location(path, int(m.group(2))))
    return tuple(dict.fromkeys(found))


def repo_relative(path: str) -> str:
    """The repository-relative form of a path, or "" when it points outside the repository."""
    if any(marker in path for marker in _FOREIGN):
        return ""
    stripped = _WORKSPACE.sub("", path)
    return "" if stripped.startswith(("/", "..")) else stripped


def test_file(test_id: str) -> str:
    """The test's file when its id names one (pytest), else ""."""
    head = test_id.split("::", 1)[0]
    return head if head != test_id and head.endswith(".py") else ""


def lines_with(raw: str, needle: str, context: int = 2) -> list[str]:
    """Clean lines containing `needle`, ±`context` lines (history logs are reduced to these)."""
    lines = clean(raw)
    keep = sorted({j for i, line in enumerate(lines) if needle in line
                   for j in range(max(0, i - context), min(len(lines), i + context + 1))})
    return [lines[j] for j in keep]
