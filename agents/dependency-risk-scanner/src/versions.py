"""Pure: version parsing and ordering per ecosystem. The model never compares versions; this does.

- pip: PEP 440 (`packaging`).
- npm, cargo, go: semver (go with an optional `v`); a pre-release sorts before its release.
- maven (and gradle): numeric segments, trailing zeros ignored, then the qualifier order
  alpha < beta < milestone < rc < snapshot < (release) < sp.
- gem: numeric segments; a segment with letters makes it a pre-release.

`parse` returns None for anything it cannot read (a tag, a variable, a URL): the caller reports
`unknown`, never a guess. Versions of different ecosystems are never compared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from packaging.version import InvalidVersion
from packaging.version import Version as Pep440

ECOSYSTEMS = ("pip", "npm", "go", "cargo", "maven", "gem")
_ALIASES = {
    "pip": "pip", "pypi": "pip", "python": "pip",
    "npm": "npm", "node": "npm", "nodejs": "npm", "javascript": "npm", "yarn": "npm",
    "go": "go", "golang": "go",
    "cargo": "cargo", "crates.io": "cargo", "crates": "cargo", "rust": "cargo",
    "maven": "maven", "gradle": "maven", "java": "maven",
    "gem": "gem", "rubygems": "gem", "ruby": "gem", "bundler": "gem",
}
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_MAVEN_OK = re.compile(r"^[0-9][0-9A-Za-z.\-_]*$")
_MAVEN_TOKEN = re.compile(r"\d+|[A-Za-z]+")
_QUALIFIERS = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "milestone": 2, "m": 2, "rc": 3, "cr": 3, "snapshot": 4,
               "": 5, "ga": 5, "final": 5, "release": 5, "sp": 6}
_UNKNOWN_QUALIFIER = 5
_GEM_OK = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z]+)*$")


@dataclass(frozen=True, order=True)
class Version:
    key: tuple[Any, ...]
    text: str = field(compare=False)
    ecosystem: str = field(compare=False)
    major: int = field(compare=False, default=0)


def ecosystem(word: str | None) -> str | None:
    return _ALIASES.get((word or "").strip().casefold())


def parse(eco: str, text: str | None) -> Version | None:
    raw = (text or "").strip()
    if not raw:
        return None
    parser = _PARSERS.get(eco)
    return parser(raw, eco) if parser else None


def major(version: Version) -> int:
    return version.major


def _pip(raw: str, eco: str) -> Version | None:
    try:
        parsed = Pep440(raw)
    except InvalidVersion:
        return None
    return Version(("pep440", parsed), raw, eco, parsed.release[0] if parsed.release else 0)


def _semver(raw: str, eco: str) -> Version | None:
    match = _SEMVER.match(raw)
    if match is None:
        return None
    nums = tuple(int(match.group(i)) for i in (1, 2, 3))
    pre = match.group(4)
    pre_key = (1, ()) if pre is None else (0, tuple(_ident(p) for p in pre.split(".")))
    return Version((nums, pre_key), raw, eco, nums[0])


def _ident(part: str) -> tuple[int, int, str]:
    return (0, int(part), "") if part.isdigit() else (1, 0, part)


def _trim(nums: tuple[int, ...]) -> tuple[int, ...]:
    out = list(nums)
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return tuple(out)


def _maven(raw: str, eco: str) -> Version | None:
    if not _MAVEN_OK.match(raw):
        return None
    tokens = _MAVEN_TOKEN.findall(raw)
    nums: list[int] = []
    while tokens and tokens[0].isdigit():
        nums.append(int(tokens.pop(0)))
    qualifier = tokens.pop(0).casefold() if tokens and not tokens[0].isdigit() else ""
    rank = _QUALIFIERS.get(qualifier, _UNKNOWN_QUALIFIER)
    qual_num = int(tokens.pop(0)) if tokens and tokens[0].isdigit() else 0
    rest = tuple(_ident(t) for t in tokens)
    return Version((_trim(tuple(nums)), rank, qual_num, rest), raw, eco, nums[0] if nums else 0)


def _gem(raw: str, eco: str) -> Version | None:
    if not _GEM_OK.match(raw):
        return None
    segments = raw.split(".")
    nums: list[int] = []
    while segments and segments[0].isdigit():
        nums.append(int(segments.pop(0)))
    pre = tuple(_ident(s) for s in segments)
    return Version((_trim(tuple(nums)), 0 if pre else 1, pre), raw, eco, nums[0] if nums else 0)


_PARSERS = {"pip": _pip, "npm": _semver, "cargo": _semver, "go": _semver, "maven": _maven, "gem": _gem}
