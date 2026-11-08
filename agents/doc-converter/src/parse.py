"""Resolve the request sentence into slots: where the file is and its name or link.

Decided in code first (a place named in words or by a link's host, literal file names, links). Only when
something is still unknown does the ONE model call run; its answer is kept only where each name and link
appears verbatim in the user's text."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import prompts
import sources
from deps import TurnDeps
from inputs import MAX_FILES, SHEET_OPTION
from nasiko_trace import bound_traceparent

log = logging.getLogger("doc_converter.parse")

CALL_TIMEOUT_SECS = 60.0
MAX_TOKENS = 300
EXTENSIONS = ("pdf", "docx", "pptx", "xlsx", "xls", "csv", "tsv", "html", "htm", "json", "xml", "txt", "md",
              "epub", "zip")
_URL = re.compile(r"https?://[^\s<>\"'`)\]]+")
_QUOTED = re.compile(r"[\"“]([^\"”\n]{1,200})[\"”]|(?:(?<=\s)|^)['‘]([^'’\n]{1,200})['’](?=[\s.,;:!?]|$)")
_FILE_NAME = re.compile(r"(?<![\w/.-])([\w][\w.()&+-]{0,150}?\.(?:" + "|".join(EXTENSIONS) + r"))\b",
                        re.IGNORECASE)
_LEADING_WORDS = re.compile(r"^(?:(?:please|convert|turn|the|my|file|doc|document|deck|sheet|spreadsheet|"
                            r"and|also|then|to|this|that|called|named)\s+)+", re.IGNORECASE)


class ParseError(RuntimeError):
    """The model could not be reached or never returned the contract."""


@dataclass(frozen=True)
class Request:
    status: str  # "ok" | "off_topic"
    place: str | None = None  # "drive" | "dropbox" | "pasted" | None
    files: tuple[dict, ...] = ()  # {"query": str | None, "link": str | None}


def literal(text: str) -> Request:
    """What code alone can read: a place named in words or by a link, file names and links."""
    text = SHEET_OPTION.sub(" ", text)
    links = tuple(dict.fromkeys(_URL.findall(text)))
    place = next((p for p in (sources.place_of_link(u) for u in links) if p), None) or sources.place_in_words(text)
    names = [_clean(a or b) for a, b in _QUOTED.findall(text)]
    without_quotes = _QUOTED.sub(" ", _URL.sub(" ", text))
    names += [_clean(m) for m in _FILE_NAME.findall(without_quotes)]
    files = [{"query": None, "link": u} for u in links] + [{"query": n, "link": None} for n in dict.fromkeys(names) if n]
    return Request("ok", place, tuple(files[:MAX_FILES]))


def needs_model(found: Request) -> bool:
    return not (found.place and found.files)


async def resolve(deps: TurnDeps, text: str) -> Request:
    """The one model call. Raises ParseError when the model is unreachable or keeps breaking the contract."""
    messages = [{"role": "system", "content": prompts.PARSE_SYSTEM}, {"role": "user", "content": prompts.fence(text)}]
    raw = await _call(deps, messages)
    parsed = validate(_load(raw), text)
    if parsed is None:
        log.info("parse answer failed its contract; one repair call")
        parsed = validate(_load(await _call(deps, prompts.repair_messages(raw))), text)
    if parsed is None:
        raise ParseError("the model's answer did not match the contract")
    return parsed


def validate(data: Any, text: str) -> Request | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "off_topic":
        return Request("off_topic")
    place = data.get("source") if data.get("source") in ("drive", "dropbox", "pasted") else None
    lowered = text.casefold()
    files = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        query = _verbatim(item.get("query"), lowered)
        link = _verbatim(item.get("link"), lowered)
        if query or link:
            files.append({"query": query, "link": link})
    return Request("ok", place, tuple(files[:MAX_FILES]))


def merge(code: Request, model: Request) -> Request:
    """Code wins: its place and names stand; the model only fills what code left empty."""
    if model.status != "ok":
        return model
    return Request("ok", code.place or model.place, code.files or model.files)


def _verbatim(value: Any, lowered_text: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.strip()
    return clean if clean.casefold() in lowered_text else None


def _clean(name: str) -> str:
    return _LEADING_WORDS.sub("", name.strip()).strip(" ,.")


async def _call(deps: TurnDeps, messages: list[dict]) -> str:
    if not deps.traceparent:
        raise ParseError("no inbound traceparent")
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=messages, max_tokens=MAX_TOKENS, temperature=0,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise ParseError(f"model call failed: {type(e).__name__}") from e
    return str(response.choices[0].message.content or "")


def _load(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
