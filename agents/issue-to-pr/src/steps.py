"""The model calls (JSON, tool-less): scope gate, pick_files, pick_symbols, locate, kind. Each prompt fences
the issue and the repository data; every answer is validated for shape here and for content in checks.py."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import llm
import prompts
from candidates import Candidate
from fence import fence
from llm import TurnDeps

ISSUE_DIGEST_CHARS = 6_000
LOCATE_CHARS_PER_CALL = 12_000
MAX_LOCATE_CALLS = 2
MAX_SYMBOL_LINES = 250


class OffTopic(Exception):
    """The model returned the off-topic sentinel."""


def _shape(key: str):
    def validate(data: Any) -> dict | None:
        if llm.is_off_topic(data):
            return {"status": "off_topic"}
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data
        return None
    return validate


def _check(data: dict) -> dict:
    if llm.is_off_topic(data):
        raise OffTopic()
    return data


def issue_digest(text: str) -> str:
    return fence("ISSUE", text, ISSUE_DIGEST_CHARS)


async def gate(deps: TurnDeps, request: str) -> bool:
    """True when the message is in scope (one tiny call, used only when it names no issue, repo or file)."""
    messages = [{"role": "system", "content": prompts.GATE_SYSTEM}, {"role": "user", "content": fence("INPUT", request)}]

    def validate(data: Any) -> dict | None:
        return data if isinstance(data, dict) and data.get("status") in ("ok", "off_topic") else None

    data = await llm.complete_json(deps, messages, '{"status":"ok|off_topic"}', validate, max_tokens=20)
    return data.get("status") == "ok"


async def pick_files(deps: TurnDeps, issue_text: str, candidates: Sequence[Candidate]) -> dict:
    rows = "\n".join(f"{c.path} | bm25 rank {c.bm25_rank or '-'} | search hits {c.hits} | "
                     f"{'named in issue' if c.mentioned else '-'}" for c in candidates)
    user = f"{issue_digest(issue_text)}\n\n{fence('CANDIDATES', rows)}"
    messages = [{"role": "system", "content": prompts.PICK_FILES_SYSTEM}, {"role": "user", "content": user}]
    return _check(await llm.complete_json(deps, messages, prompts.contract_of(prompts.PICK_FILES_SYSTEM),
                                          _shape("files")))


async def pick_symbols(deps: TurnDeps, issue_text: str, skeletons: Mapping[str, str]) -> dict:
    blocks = "\n\n".join(f"## {path}\n{decls}" for path, decls in skeletons.items())
    user = f"{issue_digest(issue_text)}\n\n{fence('SKELETONS', blocks)}"
    messages = [{"role": "system", "content": prompts.PICK_SYMBOLS_SYSTEM}, {"role": "user", "content": user}]
    return _check(await llm.complete_json(deps, messages, prompts.contract_of(prompts.PICK_SYMBOLS_SYSTEM),
                                          _shape("files")))


async def locate(deps: TurnDeps, issue_text: str, code_blocks: Sequence[tuple[str, str, str]]) -> dict:
    """`code_blocks` = (path, symbol, code). Split across at most 2 calls of ≤ 12k chars; answers merged."""
    batches = _batches(code_blocks)
    merged: list[dict] = []
    for batch in batches[:MAX_LOCATE_CALLS]:
        body = "\n\n".join(f"## {path} :: {symbol}\n{code}" for path, symbol, code in batch)
        user = f"{issue_digest(issue_text)}\n\n{fence('CODE', body, LOCATE_CHARS_PER_CALL + 2_000)}"
        messages = [{"role": "system", "content": prompts.LOCATE_SYSTEM}, {"role": "user", "content": user}]
        data = _check(await llm.complete_json(deps, messages, prompts.contract_of(prompts.LOCATE_SYSTEM),
                                              _shape("locations")))
        merged += [i for i in data["locations"] if isinstance(i, dict)]
    return {"status": "ok", "locations": merged}


async def kind(deps: TurnDeps, issue_text: str) -> str:
    messages = [{"role": "system", "content": prompts.KIND_SYSTEM}, {"role": "user", "content": issue_digest(issue_text)}]

    def validate(data: Any) -> dict | None:
        return data if isinstance(data, dict) and isinstance(data.get("kind"), str) else None

    return str((await llm.complete_json(deps, messages, prompts.contract_of(prompts.KIND_SYSTEM), validate,
                                        max_tokens=30))["kind"])


def _batches(blocks: Sequence[tuple[str, str, str]]) -> list[list[tuple[str, str, str]]]:
    batches: list[list[tuple[str, str, str]]] = [[]]
    size = 0
    for block in blocks:
        length = len(block[2]) + len(block[0]) + len(block[1])
        if batches[-1] and size + length > LOCATE_CHARS_PER_CALL:
            batches.append([])
            size = 0
        batches[-1].append(block)
        size += length
    return [b for b in batches if b]
