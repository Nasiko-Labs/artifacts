"""Pure: version ranges as unions of intervals, parsed per ecosystem, with membership and overlap.

Affected ranges come from an advisory: comparator strings (`>=a,<b`, `<b`, `=x`, `a - b`, `||` for
"or") or advisory-database events (`introduced`, `fixed`, `last_affected`). Declared ranges come from a
manifest: npm/cargo caret and tilde (a bare cargo version is a caret), pip `~=` and `==1.4.*`, gem
`~>`, maven `[a,b)` and gradle `1.+`, comparator sets. Anything unreadable is None, never a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import versions
from versions import Version

_SEMVER_ECOS = ("npm", "cargo", "go")
_OPS = r"(>=|<=|==|!=|~=|~>|>|<|=|\^|~)"
_OP_SPACE = re.compile(_OPS + r"\s+")
_TOKEN = re.compile(_OPS + r"?(v?[0-9][0-9A-Za-z.+\-]*?(?:\.[*xX+])?|[*xX])$")
_HYPHEN = re.compile(r"^\s*(v?[0-9][0-9A-Za-z.+\-]*)\s+-\s+(v?[0-9][0-9A-Za-z.+\-]*)\s*$")
_MAVEN_RANGE = re.compile(r"([\[(])\s*([^,\])]*?)\s*(?:,\s*([^\])]*?)\s*)?([\])])")
_WILDCARD = re.compile(r"^(v?)((?:\d+\.)*)(?:[*xX+])$")


@dataclass(frozen=True)
class Interval:
    lo: Version | None = None
    lo_incl: bool = True
    hi: Version | None = None
    hi_incl: bool = False


Intervals = tuple[Interval, ...]
ANY: Intervals = (Interval(),)


@dataclass(frozen=True)
class Declared:
    intervals: Intervals
    minimum: Version | None
    exact: bool
    raw: str


# --- membership and overlap -------------------------------------------------------------------------

def contains(intervals: Iterable[Interval], v: Version) -> bool:
    return any(_inside(i, v) for i in intervals)


def _inside(i: Interval, v: Version) -> bool:
    above = i.lo is None or v > i.lo or (i.lo_incl and v == i.lo)
    below = i.hi is None or v < i.hi or (i.hi_incl and v == i.hi)
    return above and below


def overlaps(a: Iterable[Interval], b: Iterable[Interval]) -> bool:
    right = tuple(b)
    return any(_intersect(x, y) is not None for x in a for y in right)


def _intersect(x: Interval, y: Interval) -> Interval | None:
    lo, lo_incl = _max_lo(x, y)
    hi, hi_incl = _min_hi(x, y)
    if lo is None or hi is None or lo < hi or (lo == hi and lo_incl and hi_incl):
        return Interval(lo, lo_incl, hi, hi_incl)
    return None


def _max_lo(x: Interval, y: Interval) -> tuple[Version | None, bool]:
    if x.lo is None:
        return y.lo, y.lo_incl
    if y.lo is None or x.lo > y.lo:
        return x.lo, x.lo_incl
    if y.lo > x.lo:
        return y.lo, y.lo_incl
    return x.lo, x.lo_incl and y.lo_incl


def _min_hi(x: Interval, y: Interval) -> tuple[Version | None, bool]:
    if x.hi is None:
        return y.hi, y.hi_incl
    if y.hi is None or x.hi < y.hi:
        return x.hi, x.hi_incl
    if y.hi < x.hi:
        return y.hi, y.hi_incl
    return x.hi, x.hi_incl and y.hi_incl


# --- affected ranges ----------------------------------------------------------------------------------

def parse_affected(eco: str, text: str) -> Intervals | None:
    """`>=4.0.0,<4.17.21`, `<4.17.21`, `= 1.2.3`, `1.0 - 1.4`, `a || b`. A bare version means exactly it."""
    alternatives = [a for a in (text or "").split("||") if a.strip()]
    if not alternatives:
        return None
    parsed = [_alternative(eco, a, declared=False) for a in alternatives]
    return None if any(p is None for p in parsed) else tuple(p for p in parsed if p is not None)


def from_events(eco: str, events: Sequence[Mapping[str, str]]) -> Intervals | None:
    """Advisory-database `events`: introduced → start; fixed → end (exclusive); last_affected → end (inclusive)."""
    out: list[Interval] = []
    lo: Version | None = None
    open_ = False
    for event in events:
        kind, value = next(iter(event.items()), ("", ""))
        if kind == "introduced":
            lo, open_ = (None if str(value) == "0" else versions.parse(eco, str(value))), True
            if str(value) != "0" and lo is None:
                return None
        elif kind in ("fixed", "last_affected"):
            end = versions.parse(eco, str(value))
            if end is None:
                return None
            out.append(Interval(lo, True, end, kind == "last_affected"))
            open_ = False
    if open_:
        out.append(Interval(lo, True, None, False))
    return tuple(out) or None


# --- declared ranges ----------------------------------------------------------------------------------

def parse_declared(eco: str, spec: str) -> Declared | None:
    raw = (spec or "").strip().strip("'\"")
    if raw in ("", "*", "x", "X"):
        return Declared(ANY, None, False, raw)
    if eco == "maven" and raw[:1] in "[(":
        intervals = _maven_ranges(raw)
    else:
        alternatives = [a for a in raw.split("||") if a.strip()]
        parsed = [_alternative(eco, a, declared=True) for a in alternatives]
        intervals = None if not parsed or any(p is None for p in parsed) else tuple(p for p in parsed if p)
    if intervals is None:
        return None
    lows = [i.lo for i in intervals]
    minimum = None if any(lo is None for lo in lows) else min(lo for lo in lows if lo is not None)
    exact = len(intervals) == 1 and intervals[0].lo is not None and intervals[0].lo == intervals[0].hi
    return Declared(intervals, minimum, exact, raw)


def _maven_ranges(raw: str) -> Intervals | None:
    out = []
    for open_b, low, high, close_b in _MAVEN_RANGE.findall(raw):
        lo = versions.parse("maven", low) if low else None
        hi_text = low if high == "" and close_b == "]" and "," not in raw else high
        hi = versions.parse("maven", hi_text) if hi_text else None
        if (low and lo is None) or (hi_text and hi is None):
            return None
        out.append(Interval(lo, open_b == "[", hi, close_b == "]"))
    return tuple(out) or None


def _alternative(eco: str, text: str, declared: bool) -> Interval | None:
    hyphen = _HYPHEN.match(text)
    if hyphen:
        lo, hi = versions.parse(eco, hyphen.group(1)), versions.parse(eco, hyphen.group(2))
        return Interval(lo, True, hi, True) if lo and hi else None
    tokens = [t for t in re.split(r"[,\s]+", _OP_SPACE.sub(r"\1", text.strip())) if t]
    if not tokens:
        return None
    result = Interval()
    for token in tokens:
        match = _TOKEN.match(token)
        if match is None:
            return None
        op = match.group(1) or ""
        if op == "!=":
            continue  # an exclusion never moves the bounds this agent uses
        constraint = _constraint(eco, op, match.group(2), declared)
        if constraint is None:
            return None
        result = _intersect(result, constraint)
        if result is None:
            return None
    return result


def _constraint(eco: str, op: str, text: str, declared: bool) -> Interval | None:
    wildcard = _WILDCARD.match(text)
    if wildcard or text in ("*", "x", "X"):
        return _prefix_range(eco, wildcard.group(2).rstrip(".") if wildcard else "")
    v = versions.parse(eco, padded(eco, text))
    if v is None:
        return None
    if op == "" and eco == "cargo" and declared:
        op = "^"
    if op in (">=", ">"):
        return Interval(v, op == ">=", None)
    if op in ("<=", "<"):
        return Interval(None, True, v, op == "<=")
    if op in ("", "=", "=="):
        return Interval(v, True, v, True)
    upper = _upper(eco, op, text)
    return Interval(v, True, upper, False) if upper else None


def _prefix_range(eco: str, prefix: str) -> Interval | None:
    if not prefix:
        return Interval()
    nums = [int(p) for p in prefix.split(".")]
    lo = versions.parse(eco, padded(eco, prefix))
    hi = versions.parse(eco, _join(eco, [*nums[:-1], nums[-1] + 1]))
    return Interval(lo, True, hi, False) if lo and hi else None


def _numbers(text: str) -> list[int]:
    head = re.match(r"v?(\d+(?:\.\d+)*)", text)
    return [int(p) for p in head.group(1).split(".")] if head else []


def _upper(eco: str, op: str, text: str) -> Version | None:
    nums = _numbers(text)
    if not nums:
        return None
    if op == "^":
        index = next((i for i, n in enumerate(nums) if n != 0), len(nums) - 1)
        bumped = [*nums[:index], nums[index] + 1]
    elif op == "~":
        bumped = [nums[0] + 1] if len(nums) == 1 else [nums[0], nums[1] + 1]
    elif op in ("~=", "~>"):
        if len(nums) < 2:
            return None
        bumped = [*nums[:-2], nums[-2] + 1]
    else:
        return None
    return versions.parse(eco, _join(eco, bumped))


def _join(eco: str, nums: list[int]) -> str:
    parts = (nums + [0, 0, 0])[:3] if eco in _SEMVER_ECOS else nums
    return ".".join(str(n) for n in parts)


def padded(eco: str, text: str) -> str:
    """Semver needs three parts: `^1.2` is `^1.2.0`."""
    if eco not in _SEMVER_ECOS or re.match(r"^v?\d+\.\d+\.\d+", text):
        return text
    nums = _numbers(text)
    prefix = "v" if text.startswith("v") else ""
    return prefix + _join(eco, nums) if nums and re.fullmatch(r"v?\d+(?:\.\d+)?", text) else text
