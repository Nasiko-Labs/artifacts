"""Pure: the chat text for a split, a merge and the follow-ups. MAF and the chat read text only, so every
part is under its own `=== PART i/N ===` heading; a reply that would pass CHAT_MAX_CHARS shows part 1 in
full and the rest as one line each ("show part i" prints one)."""

from __future__ import annotations

import replies
from envelope import fenced
from merge import MergeResult
from prompts_out import MERGE_INSTRUCTION
from split import SplitResult

CHAT_MAX_CHARS = 20_000
PREVIEW_WORDS = 8


def split_reply(result: SplitResult) -> str:
    prompts = [p["prompt"] for p in result.parts]
    if sum(len(p) for p in prompts) > CHAT_MAX_CHARS and len(prompts) > 1:
        body = "\n\n".join([prompts[0], *(collapsed(p) for p in result.parts[1:])])
    else:
        body = "\n\n".join(prompts)
    tail = [] if len(result.parts) == 1 else [f"Merge instruction: {MERGE_INSTRUCTION}"]
    return _join([result.summary, body, *tail, *result.notes, replies.TOKEN_NOTE])


def collapsed(part: dict) -> str:
    return (f"PART {part['n']}/{part['of']} · {part['chars']:,} chars · {part['label']} "
            f"(say \"show part {part['n']}\")")


def part_list(result: SplitResult) -> str:
    lines = [result.summary]
    for part in result.parts:
        words = _preview(part["prompt"])
        flag = " · cut mid-unit" if part["split_mid_unit"] else ""
        lines.append(f"- PART {part['n']}/{part['of']} · {part['chars']:,} chars (~{part['est_tokens']:,} tokens) · "
                     f"{part['label']}{flag} · starts \"{words}\"")
    return "\n".join(lines)


def merge_reply(result: MergeResult) -> str:
    notes = result.notes
    line = "Merge notes: " + "; ".join(notes) + "." if notes else "Merge notes: no duplicates, nothing ignored."
    head = f"Merged {len(result.objects)} results into one with the same schema."
    return _join([head, fenced(result.envelope), line])


def merge_notes(result: MergeResult) -> str:
    if not result.notes:
        return "No merge notes: no duplicates were removed and nothing was ignored."
    return "\n".join(["Merge notes:", *(f"- {n}" for n in result.notes)])


def _preview(prompt: str) -> str:
    body = prompt.split("<<<TEXT>>>\n", 1)[-1]
    words = body.split()[:PREVIEW_WORDS]
    return " ".join(words) + ("…" if len(body.split()) > PREVIEW_WORDS else "")


def _join(pieces: list[str]) -> str:
    return "\n\n".join(p for p in pieces if p)
