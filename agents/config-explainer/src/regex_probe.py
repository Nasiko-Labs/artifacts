"""Running a regular expression on test strings in a hard-limited subprocess (`python -I`, 1 s wall clock,
2 s CPU, inputs capped), so a catastrophic pattern can't stall the agent. Python `re` is the engine;
JavaScript named groups are translated and `\\d`/`\\w` made ASCII-only for the JavaScript flavour."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass

WALL_SECS = 1.0
CPU_SECS = 2
MAX_INPUT_CHARS = 2_000
FLAG_BITS = {"i": "re.IGNORECASE", "m": "re.MULTILINE", "s": "re.DOTALL", "x": "re.VERBOSE"}
_JS_NAMED = re.compile(r"\(\?<(?![=!])")
_JS_NAMED_REF = re.compile(r"\\k<(\w+)>")

_RUNNER = r"""
import json, re, sys
job = json.loads(sys.stdin.read())
flags = 0
for name in job["flags"]:
    flags |= getattr(re, name)
try:
    rx = re.compile(job["pattern"], flags)
except re.error as exc:
    print(json.dumps({"error": str(exc)})); sys.exit(0)
out = []
for s in job["strings"]:
    full = rx.fullmatch(s)
    found = full or rx.search(s)
    out.append({"string": s, "result": "full match" if full else ("partial match" if found else "no match"),
                "span": list(found.span()) if found else None,
                "groups": [g for g in found.groups()] if found else []})
print(json.dumps({"results": out}))
"""


@dataclass(frozen=True)
class RunResult:
    results: tuple[dict, ...] = ()
    error: str | None = None
    timed_out: bool = False


def engine_pattern(pattern: str, flavour: str | None) -> str:
    if flavour == "javascript":
        return _JS_NAMED_REF.sub(r"(?P=\1)", _JS_NAMED.sub("(?P<", pattern))
    return pattern


def engine_flags(flags: str, flavour: str | None) -> list[str]:
    names = [FLAG_BITS[f].split(".")[1] for f in flags if f in FLAG_BITS]
    if flavour == "javascript":
        names.append("ASCII")
    return names


def _limit_cpu() -> None:  # pragma: no cover - runs in the child
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECS, CPU_SECS))
    except (ImportError, ValueError, OSError):
        pass


def run(pattern: str, flags: str, strings: list[str] | tuple[str, ...], flavour: str | None = None) -> RunResult:
    """Match each string (full / partial / no match, span, groups). A timeout is a result, not an error."""
    job = {"pattern": engine_pattern(pattern, flavour), "flags": engine_flags(flags, flavour),
           "strings": [s[:MAX_INPUT_CHARS] for s in strings]}
    try:
        done = subprocess.run([sys.executable, "-I", "-c", _RUNNER], input=json.dumps(job), capture_output=True,
                              text=True, timeout=WALL_SECS, preexec_fn=_limit_cpu, check=False)
    except subprocess.TimeoutExpired:
        return RunResult(timed_out=True)
    if done.returncode != 0:  # killed by the CPU limit
        return RunResult(timed_out=True)
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return RunResult(error="the test run returned nothing readable")
    if "error" in data:
        return RunResult(error=data["error"])
    return RunResult(results=tuple(data["results"]))


def compile_error(pattern: str, flags: str, flavour: str | None = None) -> str | None:
    """The engine's own error for a pattern that doesn't compile, else None (no subprocess needed)."""
    bits = 0
    for name in engine_flags(flags, flavour):
        bits |= getattr(re, name)
    try:
        re.compile(engine_pattern(pattern, flavour), bits)
    except re.error as exc:
        return str(exc)
    return None
