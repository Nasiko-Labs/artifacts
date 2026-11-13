"""Pure: one trace's text into a `Trace`. The languages are tried in a fixed detection order; the first parser
that finds frames wins. Text with no frame at all is `frameless`: only its error line is parsed."""

from __future__ import annotations

import re

import frames as frames_mod
import parse_jvm
import parse_other
import parse_python
from trace_model import Block, Trace

ERROR_LINE_MAX_CHARS = 300

_PARSERS = (
    (parse_python.detect, parse_python.parse),
    (parse_other.detect_go, parse_other.parse_go),
    (parse_other.detect_rust, parse_other.parse_rust),
    (parse_jvm.detect_csharp, parse_jvm.parse_csharp),
    (parse_jvm.detect_java, parse_jvm.parse_java),
    (parse_other.detect_ruby, parse_other.parse_ruby),
    (parse_other.detect_node, parse_other.parse_node),
)

ERROR_WORDS = re.compile(
    r"\b(?:\w*errors?|\w*exceptions?|traceback|panic(?:ked)?|fatal|failed|failure|refused|denied|segmentation fault|"
    r"segfault|stack ?trace|undefined|null ?pointer|nil pointer|cannot|can't find|not found|timed? ?out|"
    r"exit (?:code|status) [1-9]\d*|core dumped|unhandled|uncaught)\b", re.IGNORECASE)
_TYPED_LINE = re.compile(r"(?P<type>[A-Za-z_][\w.$]*(?:Error|Exception|Panic|Fault))\b:?\s*(?P<message>.*)")
_ERRNO = re.compile(r"\b(?P<type>E[A-Z]{3,})\b")
_ESCAPED_NEWLINE = re.compile(r"\\n")


def has_error_text(text: str) -> bool:
    return bool(ERROR_WORDS.search(text or "") or _ERRNO.search(text or ""))


def unescape(text: str) -> str:
    """A trace copied out of a JSON log has literal `\\n` / `\\t`: turn them into real ones, but only when the
    text has no real line breaks of its own."""
    if "\n" not in text.strip() and _ESCAPED_NEWLINE.search(text):
        return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return text


def parse(text: str, trust_libraries: bool = False) -> Trace:
    """Always returns a trace: a parsed one with frames, else a frameless one built from the error line."""
    body = unescape(text or "")
    for detect, run in _PARSERS:
        if not detect(body):
            continue
        trace = run(body)
        if trace is not None and trace.all_frames():
            return frames_mod.classify(trace, trust_libraries)
    return frameless(body)


def frameless(text: str) -> Trace:
    exc_type, message = error_line(text)
    return Trace("unknown", (Block(exc_type, message),), text, frameless=True)


def error_line(text: str) -> tuple[str, str]:
    """(type, message) of the first line that looks like an error; ("Error", first line) otherwise."""
    lines = [line.strip().strip("`") for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        typed = _TYPED_LINE.search(line)
        if typed:
            return typed["type"], typed["message"].strip()[:ERROR_LINE_MAX_CHARS]
    for line in lines:
        errno = _ERRNO.search(line)
        if errno:
            return errno["type"], line[:ERROR_LINE_MAX_CHARS]
    for line in lines:
        if has_error_text(line):
            return "Error", line[:ERROR_LINE_MAX_CHARS]
    return "Error", (lines[0][:ERROR_LINE_MAX_CHARS] if lines else "")


def has_typed_error(text: str) -> bool:
    """A line like `KeyError: 'x'` / `ECONNREFUSED`: a frameless error the user pasted, not a question about one."""
    return any(_TYPED_LINE.search(line) or _ERRNO.search(line) for line in (text or "").splitlines())
