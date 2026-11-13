"""Pure: managed-runtime traces, Java/Kotlin and C#. Both print the innermost frame first.

Java: the first block is the surface exception; each `Caused by:` starts a block and the LAST one is the root cause.
`... 23 more` lines are not frames. C#: an inner exception (` ---> Type: message`) is the root cause; its frames
come first, then `--- End of inner exception stack trace ---`, then the outer exception's frames."""

from __future__ import annotations

import re

from trace_model import Block, Frame, Trace

_JAVA_FRAME = re.compile(r"^\s*at (?P<func>[\w.$<>/\-]+)\((?P<file>[^():]+)(?::(?P<line>\d+))?\)\s*$")
_JAVA_HEAD = re.compile(r"^(?:Exception in thread \"[^\"]*\"\s+)?(?P<type>[\w.$]+(?:Exception|Error|Throwable)\b"
                        r"[\w.$]*)(?::\s?(?P<message>.*))?$")
_JAVA_SIGNATURE = re.compile(r"^\s*at [\w.$<>/\-]+\([^()]*(?:\.java|\.kt|\.scala|\.groovy|Native Method|Unknown Source)"
                             r"[^()]*\)\s*$|^Exception in thread \"", re.MULTILINE)
_CAUSED = re.compile(r"^\s*Caused by:\s*(?P<rest>.*)$")
_MORE = re.compile(r"^\s*\.\.\. \d+ (?:more|common frames omitted)\s*$")

_CS_FRAME = re.compile(r"^\s*at (?P<func>[\w.<>$+`\[\],| ]+?)\(.*\) in (?P<file>.+):line (?P<line>\d+)\s*$")
_CS_SIGNATURE = re.compile(r"^\s*at .+\) in .+:line \d+\s*$|^Unhandled exception\.", re.MULTILINE)
_CS_HEAD = re.compile(r"^(?:Unhandled exception\.\s*)?(?P<type>[\w.`]+(?:Exception|Error)\b)(?::\s?(?P<message>.*))?$")
_CS_INNER = " ---> "
_CS_END_INNER = re.compile(r"^\s*--- End of inner exception stack trace ---\s*$")


def detect_java(text: str) -> bool:
    return bool(_JAVA_SIGNATURE.search(text))


def detect_csharp(text: str) -> bool:
    return bool(_CS_SIGNATURE.search(text))


def parse_java(text: str) -> Trace | None:
    blocks: list[tuple[str, str, list[Frame]]] = []
    for line in text.splitlines():
        caused = _CAUSED.match(line)
        head = _JAVA_HEAD.match((caused["rest"] if caused else line).strip())
        frame = _JAVA_FRAME.match(line)
        if frame and blocks:
            if frame["line"]:
                blocks[-1][2].append(Frame(frame["file"], int(frame["line"]), _strip_module(frame["func"])))
        elif (caused or not blocks) and head:
            blocks.append((head["type"], (head["message"] or "").strip(), []))
    if not blocks:
        return None
    built = tuple(Block(t, m, tuple(f)) for t, m, f in blocks)
    return Trace("java", built, text, root=len(built) - 1, surface=0)


def _strip_module(func: str) -> str:
    """`java.base/java.lang.Thread.run` -> `java.lang.Thread.run` (Java 9+ module prefix)."""
    return func.split("/", 1)[1] if "/" in func else func


def parse_csharp(text: str) -> Trace | None:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _CS_HEAD.match(line.split(_CS_INNER)[0].strip())), None)
    if start is None:
        return None
    heads = [lines[start].strip()]
    i = start + 1
    while i < len(lines) and lines[i].strip().startswith("--->"):
        heads.append(lines[i].strip())
        i += 1
    names = [h.strip().removeprefix("--->").strip() for head in heads for h in head.split(_CS_INNER)]
    frames: list[list[Frame]] = [[] for _ in names]
    current = len(names) - 1  # the innermost exception's frames are printed first
    for line in lines[i:]:
        if _CS_END_INNER.match(line):
            current = max(0, current - 1)
            continue
        frame = _CS_FRAME.match(line)
        if frame:
            frames[current].append(Frame(frame["file"].strip(), int(frame["line"]), frame["func"].strip()))
    blocks = []
    for name, own in zip(names, frames, strict=True):
        head = _CS_HEAD.match(name)
        exc_type, message = (head["type"], (head["message"] or "").strip()) if head else ("Exception", name)
        blocks.append(Block(exc_type, message, tuple(own)))
    return Trace("csharp", tuple(blocks), text, root=len(blocks) - 1, surface=0)
