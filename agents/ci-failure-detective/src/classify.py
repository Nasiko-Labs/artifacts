"""Pure: the failure class, decided in code by ordered pattern sets (the first class that matches wins:
infra, then timeout, then compile, then test assertion; otherwise unknown)."""

from __future__ import annotations

import re
from typing import Literal

from excerpt import clean

FailureClass = Literal["infra", "timeout", "compile", "test_assertion", "unknown"]
CLASSES: tuple[FailureClass, ...] = ("infra", "timeout", "compile", "test_assertion", "unknown")
LABELS = {"infra": "infrastructure", "timeout": "timeout", "compile": "compile / build error",
          "test_assertion": "test assertion", "unknown": "unknown"}

_DOWNLOAD = r"(?:npm|pip|yarn|pnpm|cargo|maven|gradle|registry|download|fetch|install|pull|apt|docker)"
_PATTERNS: dict[FailureClass, tuple[re.Pattern, ...]] = {
    "infra": tuple(re.compile(p, re.IGNORECASE) for p in (
        r"No space left on device",
        r"exit code 137\b|^\s*Killed\s*$|\bKilled\b.*signal 9",
        r"The runner has received a shutdown signal",
        r"lost communication with the server",
        r"Could not resolve host",
        rf"(?:ECONNRESET|ETIMEDOUT).*{_DOWNLOAD}|{_DOWNLOAD}.*(?:ECONNRESET|ETIMEDOUT)",
        rf"(?:rate limit|\b429\b).*{_DOWNLOAD}|{_DOWNLOAD}.*(?:rate limit|\b429\b)",
        r"Unable to locate executable",
    )),
    "timeout": tuple(re.compile(p) for p in (
        r"exceeded the maximum execution time",
        r"timed out after",
        r"Timeout of \d+ms exceeded",
        r"--- FAIL: .* \(\d+\.\d+s\).*timeout",
        r"panic: test timed out",
    )),
    "compile": tuple(re.compile(p) for p in (
        r"error\[E\d+\]", r"error TS\d+", r"SyntaxError", r"cannot find symbol", r"undefined: ",
        r"ModuleNotFoundError", r"ImportError while importing test module|ERROR collecting .*ImportError",
        r"[Cc]ompilation failed",
    )),
    "test_assertion": tuple(re.compile(p) for p in (
        r"AssertionError", r"assert .* ==", r"Expected: .*Received:|Expected: .*", r"expected .* got",
        r"--- FAIL:", r"FAILED .*::", r"● .* ›", r"<<< FAILURE!",
    )),
}
_BUILD_FAILED = re.compile(r"build failed", re.IGNORECASE)


def classify(raw: str, job_conclusion: str = "") -> tuple[FailureClass, str]:
    """(class, the first matching line verbatim after cleaning) for this log and job conclusion."""
    lines = clean(raw)
    for name in ("infra", "timeout", "compile", "test_assertion"):
        line = _first(lines, _PATTERNS[name])
        if line:
            return name, line
        if name == "timeout" and job_conclusion == "timed_out":
            return "timeout", ""
        if name == "compile" and (line := _build_failed_before_tests(lines)):
            return "compile", line
    return "unknown", ""


def _first(lines: list[str], patterns: tuple[re.Pattern, ...]) -> str:
    return next((line for line in lines if any(p.search(line) for p in patterns)), "")


def _build_failed_before_tests(lines: list[str]) -> str:
    for line in lines:
        if any(p.search(line) for p in _PATTERNS["test_assertion"]):
            return ""
        if _BUILD_FAILED.search(line):
            return line
    return ""


def accept_model_class(proposed: object, quote: object, raw: str) -> FailureClass:
    """When code found nothing, the model may name a class, but only with a line quoted verbatim."""
    if proposed not in CLASSES or proposed == "unknown" or not isinstance(quote, str):
        return "unknown"
    quote = quote.strip()
    return proposed if len(quote) >= 8 and quote in (raw or "") else "unknown"
