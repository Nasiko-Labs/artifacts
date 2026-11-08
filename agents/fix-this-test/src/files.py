"""Pure: the pasted files and the failure output, split apart in code.

Files come as `# file: path` blocks (the block's text may be fenced), as fenced blocks whose info string
or preceding line names a path, or as A2A file parts already turned into `# file:` blocks. Everything else
is failure output. Paths are normalised (relative, no `..`, no absolute paths or drive letters); caps:
MAX_FILES files, MAX_TOTAL_CHARS in all, MAX_FILE_CHARS each. What is left out is listed with the reason."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_FILES = 6
MAX_TOTAL_CHARS = 60_000
MAX_FILE_CHARS = 25_000
CONFIG_FILES = frozenset({"conftest.py", "pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini"})

_HEADER = re.compile(r"^\s*(?:#|//)\s*file:\s*`?([^`\s]+)`?\s*$", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```+|~~~+)\s*([^\s`]*)\s*([^\s`]*)\s*$")
_PATH_LINE = re.compile(r"^\W{0,4}([\w./\\-]+\.(?:py|ini|cfg|toml|txt))\W{0,4}:?\s*$")
_PATHY = re.compile(r"^[\w./\\-]+\.(?:py|ini|cfg|toml|txt)$")
_RUN_START = re.compile(r"^(?:=+ (?:test session starts|FAILURES|ERRORS|short test summary info).*|"
                        r"(?:FAILED|ERROR) \S+::\S+.*)$")


@dataclass(frozen=True)
class Pasted:
    failure: str
    files: dict[str, str] = field(default_factory=dict)
    skipped: tuple[tuple[str, str], ...] = ()


def normalise_path(raw: str) -> str | None:
    """A safe relative path, or None when the path is absolute, escapes upward or is malformed."""
    path = (raw or "").strip().strip("`'\"").replace("\\", "/")
    if not path or path.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", path):
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith("-") or not re.fullmatch(r"[\w.-]+", p) for p in parts):
        return None
    return "/".join(parts)


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}" or name == "tests.py"


def is_config(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in CONFIG_FILES


def split(text: str) -> Pasted:
    blocks, failure = _blocks((text or "").splitlines())
    return _capped(blocks, "\n".join(failure).strip())


def merge(files: dict[str, str], more: dict[str, str]) -> Pasted:
    return _capped([(p, t) for p, t in {**files, **more}.items()], "")


def _blocks(lines: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    blocks: list[tuple[str, str]] = []
    failure: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        header = _HEADER.match(line)
        fence = _FENCE.match(line)
        if header:
            body, i = _header_body(lines, i + 1)
            blocks.append((header.group(1), body))
            continue
        if fence and (fence.group(2) or fence.group(3) or not _closes_nothing(lines, i)):
            path = _fence_path(fence, failure)
            body, i = _fenced(lines, i + 1, fence.group(1))
            if path:
                if failure and _PATH_LINE.match(failure[-1]):
                    failure.pop()
                blocks.append((path, body))
            else:
                failure.extend(body.splitlines())
            continue
        failure.append(line)
        i += 1
    return blocks, failure


def _closes_nothing(lines: list[str], start: int) -> bool:
    return not any(_FENCE.match(line) for line in lines[start + 1:])


def _fence_path(fence: re.Match, before: list[str]) -> str | None:
    for token in (fence.group(2), fence.group(3)):
        if token and _PATHY.match(token):
            return token
    match = _PATH_LINE.match(before[-1]) if before else None
    return match.group(1) if match else None


def _fenced(lines: list[str], start: int, marker: str) -> tuple[str, int]:
    body = []
    i = start
    while i < len(lines) and not lines[i].strip().startswith(marker[:3]):
        body.append(lines[i])
        i += 1
    return "\n".join(body) + "\n", i + 1


def _header_body(lines: list[str], start: int) -> tuple[str, int]:
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    fence = _FENCE.match(lines[i]) if i < len(lines) else None
    if fence:
        return _fenced(lines, i + 1, fence.group(1))
    body = []
    while i < len(lines) and not _ends_block(lines, i):
        body.append(lines[i])
        i += 1
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) + "\n", i


def _ends_block(lines: list[str], i: int) -> bool:
    """An unfenced `# file:` block ends at the next header, fence, `path:` line before a fence, or pytest run."""
    line = lines[i]
    if _HEADER.match(line) or _RUN_START.match(line) or _FENCE.match(line):
        return True
    return bool(_PATH_LINE.match(line)) and i + 1 < len(lines) and bool(_FENCE.match(lines[i + 1]))


def _capped(blocks: list[tuple[str, str]], failure: str) -> Pasted:
    files: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    total = 0
    for raw, body in blocks:
        path = normalise_path(raw)
        if path is None:
            skipped.append((raw, "unsafe path (absolute, `..` or unusual characters)"))
        elif len(body) > MAX_FILE_CHARS:
            skipped.append((path, f"over {MAX_FILE_CHARS:,} characters"))
        elif path not in files and len(files) >= MAX_FILES:
            skipped.append((path, f"more than {MAX_FILES} files"))
        elif total + len(body) - len(files.get(path, "")) > MAX_TOTAL_CHARS:
            skipped.append((path, f"over {MAX_TOTAL_CHARS:,} characters in all"))
        else:
            total += len(body) - len(files.get(path, ""))
            files[path] = body
    return Pasted(failure, files, tuple(skipped))


def resolve(frame_path: str, files: dict[str, str]) -> str | None:
    """The pasted file a traceback path refers to (exact, or one is a path suffix of the other)."""
    clean = frame_path.replace("\\", "/").lstrip("./")
    for path in files:
        if clean == path or clean.endswith("/" + path) or path.endswith("/" + clean):
            return path
    return None


def missing(frames: tuple[tuple[str, int], ...], test_ids: tuple[str, ...], files: dict[str, str]) -> list[str]:
    """Project files the failure names that were not pasted (library frames are ignored)."""
    wanted = [tid.split("::", 1)[0] for tid in test_ids if "::" in tid] + [p for p, _ in frames]
    out: list[str] = []
    for raw in wanted:
        if raw.startswith(("/", "<")) or "site-packages" in raw or "/lib/python" in raw or "_pytest" in raw:
            continue
        path = normalise_path(raw)
        if path and resolve(path, files) is None and path not in out:
            out.append(path)
    return out
