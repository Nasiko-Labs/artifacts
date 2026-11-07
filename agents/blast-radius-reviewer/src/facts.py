"""The census record every probe reads: one thing the diff changed, with the verbatim token to search for."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    kind: str                 # symbol, literal, env, config_key, wire, sql, dep, lock, deleted, timing, ...
    file: str
    line: int | None
    token: str
    change: str               # removed | added | changed | renamed
    detail: str = ""
    flags: tuple[str, ...] = ()
