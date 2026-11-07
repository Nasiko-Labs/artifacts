"""Pure: the reply text. The document, a one-line status and the coverage notes; or the envelope as one
fenced JSON block when JSON was asked. A reply over 90,000 characters is split in two on a section
boundary."""

from __future__ import annotations

import json
from collections.abc import Sequence

import assemble

MAX_PART_CHARS = 90_000
EXAMPLE_ANSWER = "`Q2: 500 requests/second`"
DRAFT_NOTE = "_This is a draft to copy; nothing was written anywhere._"


def status_line(env: dict) -> str:
    cov, qs = env["coverage"], env["open_questions"]
    head = (f"Draft: {cov['requirements']} requirements, {cov['components']} components, "
            f"{cov['acceptance_criteria']} acceptance criteria.")
    if not qs:
        return head + " No open questions."
    noun = "question needs" if len(qs) == 1 else "questions need"
    return (f"{head} {len(qs)} open {noun} your answer; answer by number (e.g. {EXAMPLE_ANSWER}) and I'll update "
            "only the affected sections.")


def notes(env: dict, skipped_chars: int, generic: Sequence[str], extra: Sequence[str] = ()) -> list[str]:
    out = list(extra)
    dropped = env.get("dropped") or {}
    if dropped.get("requirements"):
        out.append(f"_{dropped['requirements']} requirement(s) left out: their quoted source text wasn't in your "
                   "input._")
    if dropped.get("steps"):
        out.append(f"_{dropped['steps']} flow step(s) or contract(s) left out: they named a component that isn't in "
                   "the components table._")
    if skipped_chars:
        out.append(f"_I read the first part of your input; the last {skipped_chars:,} characters were skipped "
                   "(size cap). Paste them in a follow-up to add them._")
    if generic:
        out.append(f"_Unnamed technologies replaced by generic terms: {', '.join(generic)}._")
    return out


def render(parts: Sequence[tuple[str, str]], env: dict, extra_notes: Sequence[str] = ()) -> list[str]:
    tail = "\n\n".join([status_line(env), *extra_notes, DRAFT_NOTE])
    whole = assemble.join(parts) + "\n\n---\n" + tail
    if len(whole) <= MAX_PART_CHARS:
        return [whole]
    cut = _split_point(parts)
    first = assemble.join(parts[:cut]) + "\n\n_(continued in part 2)_"
    return [first, "_(part 2)_\n\n" + assemble.join(parts[cut:]) + "\n\n---\n" + tail]


def _split_point(parts: Sequence[tuple[str, str]]) -> int:
    sizes = [len(h) + len(b) + 4 for h, b in parts]
    half, total = sum(sizes) / 2, 0
    for i, size in enumerate(sizes):
        total += size
        if total >= half:
            return max(1, min(i + 1, len(parts) - 1))
    return max(1, len(parts) - 1)


def render_json(env: dict) -> list[str]:
    return ["```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"]
