"""Pure, tokenizer-level PromQL checks (no PromQL parser): durations, comparison operators, range windows,
counters outside rate(), `by (...)` labels, and the normalised form duplicates are compared on."""

from __future__ import annotations

import re

_UNIT_SECS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_DURATION = re.compile(r"^\s*((?:\d+(?:\.\d+)?(?:ms|s|m|h|d|w|y))+)\s*$")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)")
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
_BRACES = re.compile(r"\{[^{}]*\}")
_RANGE = re.compile(r"\[[^\]]*\]")
_COMPARISON = re.compile(r">=|<=|==|!=|>|<")
_BOOL = re.compile(r"\bbool\b")
_ABSENT = re.compile(r"\babsent(?:_over_time)?\s*\(")
_UP_DOWN = re.compile(r"^\s*up\s*(\{[^}]*\})?\s*==\s*0\s*$")
_ABSENT_UP = re.compile(r"\babsent\s*\(\s*up\b")
_COUNTER_FUNCS = r"rate|irate|increase|delta|idelta|resets|changes|deriv"
_COUNTER_CALL = re.compile(rf"\b(?:{_COUNTER_FUNCS})\s*\(")
_WINDOW_CALL = re.compile(r"\b(rate|irate|increase)\s*\(")
_COUNTER_NAME = re.compile(r"(?<![\w:])([a-zA-Z_:][\w:]*_total)\b")
_BY = re.compile(r"\bby\s*\(([^)]*)\)")
_NUMBER = re.compile(r"(?<![\w.\[$])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w\]])")
_SPACE = re.compile(r"\s+")
_ZERO_GAUGE = re.compile(r"^\s*([a-zA-Z_:][\w:]*)\s*(\{[^}]*\})?\s*(>=|<=|==|!=|>|<)\s*0(?:\.0+)?\s*$")
_COUNTER_SUFFIX = ("_total", "_count", "_sum", "_bucket")
HIGH_CARDINALITY = frozenset({"container_id", "user", "user_id", "request_id", "trace_id", "session_id"})
NODE_LABELS = frozenset({"instance", "pod", "container", "node"})


def duration_secs(value) -> int | None:
    """`5m` -> 300, `1h30m` -> 5400, `0` / `0s` -> 0, a number of seconds -> int; None when unreadable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    if not _DURATION.match(text):
        return None
    return int(sum(float(n) * _UNIT_SECS[u] for n, u in _DURATION_PART.findall(text)))


def fmt_duration(secs: int) -> str:
    if secs and secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs and secs % 60 == 0:
        return f"{secs // 60}m"
    return f"{secs}s"


def _bare(expr: str) -> str:
    """The expression without strings, label matchers and range windows."""
    return _RANGE.sub("", _BRACES.sub("", _STRING.sub('""', expr or "")))


def has_comparison(expr: str) -> bool:
    bare = _bare(expr)
    return bool(_COMPARISON.search(bare) or _BOOL.search(bare))


def is_absence(expr: str) -> bool:
    return bool(_ABSENT.search(_STRING.sub('""', expr or "")))


def is_up_check(expr: str) -> bool:
    """`up == 0` or `absent(up{...})`: target-down rules (AR013)."""
    return bool(_UP_DOWN.match(expr or "") or _ABSENT_UP.search(expr or ""))


def _call_bodies(expr: str, pattern: re.Pattern) -> list[tuple[str, str, int, int]]:
    """(function, body, start, end) for each call matching `pattern`, body by balanced parentheses."""
    found = []
    for match in pattern.finditer(expr):
        depth, i = 1, match.end()
        while i < len(expr) and depth:
            depth += {"(": 1, ")": -1}.get(expr[i], 0)
            i += 1
        found.append((match.group(0).split("(")[0].strip(), expr[match.end():i - 1], match.start(), i))
    return found


def range_windows(expr: str) -> list[tuple[str, int, str]]:
    """(function, seconds, raw) for each rate()/irate()/increase() range window."""
    windows = []
    for func, body, _, _ in _call_bodies(expr or "", _WINDOW_CALL):
        match = re.search(r"\[(\d+(?:\.\d+)?(?:ms|s|m|h|d|w|y)(?:\d+(?:ms|s|m|h|d|w|y))*)(?::[^\]]*)?\]", body)
        secs = duration_secs(match.group(1)) if match else None
        if secs is not None:
            windows.append((func, secs, match.group(1)))
    return windows


def uses_irate(expr: str) -> bool:
    return bool(re.search(r"\birate\s*\(", expr or ""))


def bare_counters(expr: str) -> list[str]:
    """`_total` metrics used outside rate()/increase()/... (they only ever grow)."""
    text = _STRING.sub('""', expr or "")
    for _, _, start, end in reversed(_call_bodies(text, _COUNTER_CALL)):
        text = text[:start] + text[end:]
    return sorted(set(_COUNTER_NAME.findall(_BRACES.sub("", text))))


def by_labels(expr: str) -> list[str]:
    labels: list[str] = []
    for group in _BY.findall(_STRING.sub('""', expr or "")):
        labels += [g.strip() for g in group.split(",") if g.strip()]
    return labels


def high_cardinality(labels: list[str]) -> bool:
    unique = set(labels)
    return bool(unique & HIGH_CARDINALITY) or len(unique & NODE_LABELS) >= 2 or len(unique) >= 4


def zero_gauge(expr: str) -> str | None:
    """The metric name when the expression is `<gauge>{...} <op> 0` (not `up`, not a counter)."""
    match = _ZERO_GAUGE.match(expr or "")
    if not match or match.group(1) == "up" or match.group(1).endswith(_COUNTER_SUFFIX):
        return None
    return match.group(1)


def _sorted_matchers(match: re.Match) -> str:
    body = match.group(0)[1:-1]
    parts = sorted(p.strip() for p in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", body) if p.strip())
    return "{" + ",".join(parts) + "}"


def normalise(expr: str, numbers: bool = True) -> str:
    """Whitespace removed (outside strings), label matchers sorted; with numbers=False each number is `N`."""
    def code_part(text: str) -> str:
        bare = _SPACE.sub("", text)
        return bare if numbers else _NUMBER.sub("N", bare)

    pieces, last = [], 0
    for match in _STRING.finditer(expr or ""):
        pieces.append(code_part(expr[last:match.start()]))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(code_part((expr or "")[last:]))
    return re.sub(r"\{[^{}]*\}", _sorted_matchers, "".join(pieces))


def numbers_in(expr: str) -> list[str]:
    return _NUMBER.findall(_RANGE.sub("", _BRACES.sub("", _STRING.sub('""', expr or ""))))
