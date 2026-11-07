"""The judge: one model call (two when the threads are long) that says, per thread, whether it asks the
same question and which reply holds the answer, as quotes. Threads go in fenced as untrusted data;
nothing in the answer is trusted until `verify` finds it verbatim."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import prompts
from deps import TurnDeps
from llm import ask_once, parse_answer
from models import Thread

log = logging.getLogger("dedup.judge")

MESSAGE_CHARS = 600
THREAD_CHARS = 4000
SPLIT_ABOVE_CHARS = 16_000
MAX_REPLIES = 25


def label(n: int) -> str:
    return f"T{n + 1}"


def render_thread(name: str, thread: Thread) -> str:
    lines = [f"{name} ({thread.channel_label})"]
    root = thread.root
    lines.append(f"First message [{root.author}]: {_cut(root.text)}" if root else "First message: (not read)")
    lines += [f"Reply [{m.author}]: {_cut(m.text)}" for m in thread.replies[:MAX_REPLIES]]
    text = "\n".join(lines)
    return text if len(text) <= THREAD_CHARS else text[:THREAD_CHARS] + "\n[thread cut]"


def _cut(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MESSAGE_CHARS else flat[:MESSAGE_CHARS] + " [cut]"


def batches(threads: Sequence[Thread], allow_split: bool) -> list[list[tuple[str, Thread]]]:
    named = [(label(n), t) for n, t in enumerate(threads)]
    total = sum(len(render_thread(name, t)) for name, t in named)
    if not allow_split or total <= SPLIT_ABOVE_CHARS or len(named) < 2:
        return [named]
    half = (len(named) + 1) // 2
    return [named[:half], named[half:]]


async def judge(deps: TurnDeps, question: str, threads: Sequence[Thread]) -> dict[str, Mapping]:
    """Thread key → the model's verdict for it (unchecked). Unknown ids and malformed entries are dropped."""
    verdicts: dict[str, Mapping] = {}
    for batch in batches(threads, allow_split=deps.llm_budget_left >= 2):
        if deps.llm_budget_left < 1:
            log.warning("LLM budget used up; %d threads not judged", len(batch))
            break
        body = "\n\n".join(render_thread(name, t) for name, t in batch)
        messages = [{"role": "system", "content": prompts.JUDGE_SYSTEM},
                    {"role": "user", "content": prompts.judge_request(question, body)}]
        answer = parse_answer(await ask_once(deps, messages, response_format={"type": "json_object"}))
        by_name = {name: t.key for name, t in batch}
        entries = answer.get("threads") if isinstance(answer, dict) else None
        for entry in entries if isinstance(entries, list) else ():
            if isinstance(entry, Mapping) and entry.get("thread_id") in by_name:
                verdicts.setdefault(by_name[entry["thread_id"]], entry)
    return verdicts
