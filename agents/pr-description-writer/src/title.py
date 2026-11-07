"""The title: `type(scope)!: subject` (conventional) or `Subject` (plain). Code checks the rules (imperative,
no trailing period, not generic, at most 72 characters); a failing subject gets one repair call, then a
word-boundary cut or a code-built subject. Every fix is noted."""

from __future__ import annotations

import logging
import re

import llm
import prompts
from llm import TurnDeps

log = logging.getLogger("pr-description-writer.title")

MAX_TITLE_CHARS = 72
_BANNED = re.compile(r"^(?:fix|update|changes?|wip|misc|stuff)\b.{0,12}$|\bphase \d\b|^moving code\b", re.IGNORECASE)
_LEADING_TYPE = re.compile(r"^\s*(?:feat|fix|refactor|perf|chore|docs|test|ci|build)(?:\([^)]*\))?!?:\s*",
                           re.IGNORECASE)
_VERBS = ("add", "fix", "update", "remove", "delete", "move", "rename", "create", "introduce", "implement",
          "refactor", "improve", "replace", "drop", "support", "allow", "handle", "extract", "split", "merge", "use",
          "make", "change", "bump", "document", "expose", "validate", "prevent", "enable", "disable", "simplify")
_INFLECTED = {f"{v}{suffix}": v for v in _VERBS for suffix in ("s", "es", "ed", "d", "ing")}
_INFLECTED.update({"fixes": "fix", "fixed": "fix", "fixing": "fix", "adding": "add", "added": "add", "adds": "add",
                   "making": "make", "using": "use", "moving": "move", "removing": "remove", "creating": "create",
                   "updating": "update", "changing": "change", "dropping": "drop", "splitting": "split"})


def prefix(change_type: str, scope: str | None, breaking: bool, conventional: bool) -> str:
    if not conventional:
        return ""
    return f"{change_type}{f'({scope})' if scope else ''}{'!' if breaking else ''}: "


def tidy(subject: str, conventional: bool) -> str:
    text = _LEADING_TYPE.sub("", " ".join((subject or "").replace("`", "").split())).strip(" .\"'")
    words = text.split(" ")
    if words and words[0].casefold() in _INFLECTED:
        words[0] = _INFLECTED[words[0].casefold()]
    text = " ".join(words)
    if not text:
        return ""
    first = text.split(" ")[0]
    keep_case = first.isupper() and len(first) > 1 or any(c.isupper() for c in first[1:])
    if conventional:
        return text if keep_case else text[0].lower() + text[1:]
    return text[0].upper() + text[1:]


def problems(subject: str, head: str) -> list[str]:
    found = []
    if not subject:
        found.append("empty")
    elif _BANNED.search(subject):
        found.append("too generic")
    if len(head + subject) > MAX_TITLE_CHARS:
        found.append(f"over {MAX_TITLE_CHARS} characters with the prefix")
    return found


def cut(subject: str, head: str) -> str:
    room = MAX_TITLE_CHARS - len(head)
    if len(subject) <= room:
        return subject
    clipped = subject[:room + 1].rsplit(" ", 1)[0] if " " in subject[:room + 1] else subject[:room]
    return clipped.rstrip(" ,;:-")


def code_subject(scope: str | None, first_file: str, conventional: bool) -> str:
    target = scope or (first_file.rsplit("/", 1)[-1] if first_file else "the code")
    return tidy(f"revise {target}", conventional)


async def finish(deps: TurnDeps, subject: str, head: str, summary: str, files: list[str], conventional: bool,
                 scope: str | None) -> tuple[str, list[str]]:
    """(the full title, notes about fixes)."""
    text = tidy(subject, conventional)
    notes: list[str] = []
    if problems(text, head) and deps.traceparent and deps.has_time() and deps.budget.take_repair():
        repaired = await _repair(deps, text, head, summary, files)
        if repaired is not None:
            text = tidy(repaired, conventional)
    if not text or _BANNED.search(text):
        text = code_subject(scope, files[0] if files else "", conventional)
        notes.append("The model's title was too generic, so I built one from the files.")
    if len(head + text) > MAX_TITLE_CHARS:
        text = cut(text, head)
        notes.append(f"Title cut to {MAX_TITLE_CHARS} characters.")
    return head + text, notes


async def _repair(deps: TurnDeps, subject: str, head: str, summary: str, files: list[str]) -> str | None:
    limit = MAX_TITLE_CHARS - len(head)
    messages = [{"role": "system", "content": prompts.SUBJECT_SYSTEM.format(limit=limit)},
                {"role": "user", "content": f"SUBJECT: {subject}\nPROBLEMS: {', '.join(problems(subject, head))}\n"
                                            f"SUMMARY: {summary}\nFILES: {', '.join(files[:20])}"}]
    try:
        data = llm.load(await llm.call(deps, messages, max_tokens=120))
    except llm.LlmError as e:
        log.info("subject repair failed: %s", e)
        return None
    value = data.get("subject") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value.strip() else None
