"""Pure: Go, Rust, Ruby and Node/JavaScript traces. All four print the innermost frame first."""

from __future__ import annotations

import re

from trace_model import Block, Frame, Trace

# Go: `panic: msg` / `fatal error: msg`, then `goroutine N [running]:` and pairs of `pkg.Func(args)` + `\t/path:line`.
_GO_SIGNATURE = re.compile(r"^(?:panic: |fatal error: |goroutine \d+ \[)", re.MULTILINE)
_GO_HEAD = re.compile(r"^(?P<type>panic|fatal error): (?P<message>.*)$")
_GO_GOROUTINE = re.compile(r"^goroutine \d+ \[")
_GO_LOCATION = re.compile(r"^\s+(?P<file>\S+\.(?:go|s)):(?P<line>\d+)(?: \+0x[0-9a-f]+)?\s*$")

# Rust: `thread 'main' panicked at src/main.rs:14:22:` + message line (1.73+), or the older quoted form.
_RUST_SIGNATURE = re.compile(r"^thread '[^']*' panicked at", re.MULTILINE)
_RUST_NEW = re.compile(r"^thread '[^']*' panicked at (?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+):\s*$")
_RUST_OLD = re.compile(r"^thread '[^']*' panicked at '(?P<message>.*)', (?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+)")
_RUST_FUNC = re.compile(r"^\s*\d+: (?P<func>\S.*)$")
_RUST_AT = re.compile(r"^\s*at (?P<file>.+):(?P<line>\d+):(?P<col>\d+)\s*$")

# Ruby: `app/x.rb:12:in 'total': message (Type)` then `\tfrom file:line:in 'caller'`.
_RUBY_FRAME = re.compile(r"(?P<file>[^:\s]+):(?P<line>\d+):in [`'](?P<func>[^'`]+)'")
_RUBY_SIGNATURE = re.compile(r"^\S+:\d+:in [`'][^'`]+'", re.MULTILINE)
_RUBY_TYPE = re.compile(r"\((?P<type>[A-Z][\w:]*)\)\s*$")

# Node: `TypeError: msg` then `    at fn (/path/file.js:14:22)`; `[cause]: Error: msg` starts a chained block.
_NODE_FRAME = re.compile(r"^\s*at (?:async )?(?:(?P<func>.+?) \()?(?P<file>[^()\s]+?):(?P<line>\d+):(?P<col>\d+)\)?\s*$")
_NODE_SIGNATURE = re.compile(r"^\s*at (?:async )?(?:.+? \()?[^()\s]+?:\d+:\d+\)?\s*$", re.MULTILINE)
_NODE_HEAD = re.compile(r"^(?:Uncaught )?(?P<type>[A-Z]?[\w.$]*(?:Error|Exception|Rejection)\b[\w.$]*)"
                        r"(?: \[(?P<code>[A-Z_]+)\])?(?::\s?(?P<message>.*))?$")
_NODE_CAUSE = re.compile(r"^\s*\[cause\]:\s*(?P<rest>.*)$")


def detect_go(text: str) -> bool:
    return bool(_GO_SIGNATURE.search(text))


def parse_go(text: str) -> Trace | None:
    lines = text.splitlines()
    exc_type, message, frames, in_goroutine, seen_goroutine = "panic", "", [], False, False
    for i, line in enumerate(lines):
        head = _GO_HEAD.match(line)
        if head and not message:
            exc_type, message = head["type"], head["message"].strip()
        if _GO_GOROUTINE.match(line):
            in_goroutine = not seen_goroutine  # only the first goroutine (the one that panicked)
            seen_goroutine = True
            continue
        location = _GO_LOCATION.match(line)
        if in_goroutine and location and i > 0:
            frames.append(Frame(location["file"], int(location["line"]), _go_func(lines[i - 1])))
    if not message and not frames:
        return None
    return Trace("go", (Block(exc_type, message, tuple(frames)),), text)


def _go_func(line: str) -> str:
    text = line.strip().removeprefix("created by ")
    text = re.sub(r" in goroutine \d+$", "", text)
    return text[:text.rfind("(")] if text.endswith(")") and "(" in text else text


def detect_rust(text: str) -> bool:
    return bool(_RUST_SIGNATURE.search(text))


def parse_rust(text: str) -> Trace | None:
    lines = text.splitlines()
    message, panic_frame, frames, func = "", None, [], ""
    for i, line in enumerate(lines):
        new, old = _RUST_NEW.match(line), _RUST_OLD.match(line)
        if new or old:
            match = new or old
            message = old["message"] if old else (lines[i + 1].strip() if i + 1 < len(lines) else "")
            panic_frame = Frame(match["file"], int(match["line"]), "", col=int(match["col"]))
            continue
        named, at = _RUST_FUNC.match(line), _RUST_AT.match(line)
        if named:
            func = named["func"].strip()
        elif at and func:
            frames.append(Frame(at["file"].strip(), int(at["line"]), func, col=int(at["col"])))
            func = ""
    if panic_frame is None:
        return None
    if not any(_same_place(f, panic_frame) for f in frames):
        frames.insert(_first_app_index(frames), panic_frame)
    return Trace("rust", (Block("panic", message, tuple(frames)),), text)


def _same_place(a: Frame, b: Frame) -> bool:
    return a.line == b.line and (a.file.endswith(b.file.lstrip("./")) or b.file.endswith(a.file.lstrip("./")))


def _first_app_index(frames: list[Frame]) -> int:
    """Where the panic location goes: before the first frame outside the std library (`/rustc/`)."""
    return next((i for i, f in enumerate(frames) if "/rustc/" not in f.file and "core::" not in f.func), len(frames))


def detect_ruby(text: str) -> bool:
    return bool(_RUBY_SIGNATURE.search(text))


def parse_ruby(text: str) -> Trace | None:
    frames, exc_type, message = [], "", ""
    for line in text.splitlines():
        stripped = line.strip().removeprefix("from ").strip()
        frame = _RUBY_FRAME.match(stripped)
        if not frame:
            continue
        frames.append(Frame(frame["file"], int(frame["line"]), frame["func"]))
        if len(frames) == 1:
            rest = stripped[frame.end():].lstrip(": ").strip()
            typed = _RUBY_TYPE.search(rest)
            exc_type = typed["type"] if typed else "RuntimeError"
            message = rest[:typed.start()].strip() if typed else rest
    if not frames:
        return None
    return Trace("ruby", (Block(exc_type, message, tuple(frames)),), text)


def detect_node(text: str) -> bool:
    return bool(_NODE_SIGNATURE.search(text))


def parse_node(text: str) -> Trace | None:
    blocks: list[tuple[str, str, list[Frame]]] = []
    for line in text.splitlines():
        cause = _NODE_CAUSE.match(line)
        frame = _NODE_FRAME.match(line)
        if frame and blocks:
            blocks[-1][2].append(Frame(frame["file"], int(frame["line"]), (frame["func"] or "").strip(),
                                       col=int(frame["col"])))
            continue
        head = _NODE_HEAD.match((cause["rest"] if cause else line).strip())
        if head and (cause or not blocks):
            message = (head["message"] or "").strip()
            blocks.append((head["type"], f"[{head['code']}] {message}".strip() if head["code"] else message, []))
    if not blocks:
        return None
    built = tuple(Block(t, m, tuple(f)) for t, m, f in blocks)
    return Trace("javascript", built, text, root=len(built) - 1, surface=0)
