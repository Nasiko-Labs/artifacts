"""Pure: recognising a follow-up on the last localization in this conversation, and the answers that need no
tool or model call (show a symbol's code, why a file, related tests, the JSON envelope again)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import triage
from skeleton_types import Symbol

MAX_SHOWN_LINES = 80

_SHOW = re.compile(r"^\s*(?:please\s+)?(?:show|open|print|display)(?:\s+me)?(?:\s+the)?(?:\s+code)?(?:\s+(?:for|of))?"
                   r"\s+`?([\w./:-]+?)`?\s*[?.!]*\s*$", re.IGNORECASE)
_WHY = re.compile(r"^\s*why\b(?:.*?`?([\w-]+(?:/[\w.-]+)+|[\w-]+\.\w{1,5})`?)?", re.IGNORECASE)
_TESTS = re.compile(r"\b(?:check|show|list|find|which|where|what)\b.*\btests?\b", re.IGNORECASE)
_LOOK = re.compile(r"\blook (?:in|at|under|inside)\s+`?([\w./-]+)`?", re.IGNORECASE)
_RERUN = re.compile(r"^\s*(?:re-?run|try again|again)\b[^:\n]*(?::\s*|\n)(.*)$", re.IGNORECASE | re.DOTALL)
_JSON_ONLY = re.compile(r"^\s*(?:(?:show|give|send)(?: me)?(?: it| that| this| the result)?\s+)?(?:as json|json only|in json)"
                        r"\s*[.!?]*\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class FollowUp:
    kind: str        # show | why | tests | look | rerun | json
    arg: str = ""


def classify(text: str) -> FollowUp | None:
    if _JSON_ONLY.match(text):
        return FollowUp("json")
    rerun = _RERUN.match(text)
    if rerun:
        return FollowUp("rerun", rerun.group(1).strip())
    look = _LOOK.search(text)
    if look and len(text.split()) <= 12:
        return FollowUp("look", look.group(1).rstrip("/."))
    show = _SHOW.match(text)
    if show:
        return FollowUp("show", show.group(1).strip("`"))
    why = _WHY.match(text)
    if why:
        return FollowUp("why", (why.group(1) or "").strip("`"))
    if _TESTS.search(text) and len(text.split()) <= 12:
        return FollowUp("tests")
    return None


def show(name: str, skeletons: Mapping[str, Sequence[Symbol]], files: Mapping[str, str]) -> str | None:
    """The code of a symbol (or a whole fetched file) with computed line numbers; None when it is not fetched."""
    for path, symbols in skeletons.items():
        for sym in symbols:
            if sym.name == name or sym.name.rsplit(".", 1)[-1] == name:
                return _code(path, sym.start, sym.end, files.get(path, ""), sym.name)
    for path, text in files.items():
        if path == name or path.endswith("/" + name):
            return _code(path, 1, len(text.split("\n")), text, None)
    return None


def why(path: str, suspects: Sequence[Mapping]) -> str:
    chosen = next((s for s in suspects if not path or s["path"] == path or s["path"].endswith("/" + path)), None)
    if chosen is None:
        return f"`{path}` is not among the suspect files I ranked."
    signals = ", ".join(chosen.get("signals") or []) or "the model's pick only"
    return (f"`{chosen['path']}` scored {chosen['score']:.2f} ({chosen['bucket']}). Signals: {signals}. "
            f"Reason: {chosen.get('why') or 'ranked by search and path signals'}.")


def tests(suspects: Sequence[Mapping], listing: Sequence[str]) -> str:
    related = triage.tests_related([s["path"] for s in suspects], listing)
    if not related:
        return "I found no test files for the suspect files in the listing I read; a new test file would be needed."
    return "Related test files in the listing:\n" + "\n".join(f"- `{t}`" for t in related)


def _code(path: str, start: int, end: int, text: str, name: str | None) -> str:
    lines = text.split("\n")
    stop = min(end, len(lines), start + MAX_SHOWN_LINES - 1)
    body = "\n".join(f"{n:>4}  {lines[n - 1]}" for n in range(start, stop + 1))
    more = f"\n… {end - stop} more lines" if end > stop else ""
    title = f"`{path}` `{name}` L{start}–L{end}" if name else f"`{path}` L{start}–L{stop}"
    return f"{title}\n```\n{body}\n```{more}"
