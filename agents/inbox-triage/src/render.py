"""User-facing text and the workflow envelope, built in code. Markdown only: no raw HTML, no images, and a
link is shown only when a tool returned it. Every count here is computed in code."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from models import Checks, Thread

TOP_N = 3
MAX_QUOTE_CHARS = 160
_UNSAFE = re.compile(r"[`*_<>|\[\]#]")
_SPACES = re.compile(r"\s+")
_SOURCE_TEXT = {"rule": "decided by a fixed rule in code", "model": "sorted by the model and checked against the email",
                "fallback": "sorted from its signals (the model gave no usable answer)", "user": "you moved it"}


@dataclass(frozen=True)
class Meta:
    """What the headline and footer say about the list."""

    label: str
    total: int
    offset: int = 0
    already_answered: int = 0
    pasted: bool = False

    def to_dict(self) -> dict:
        return {"label": self.label, "total": self.total, "offset": self.offset,
                "already_answered": self.already_answered, "pasted": self.pasted}

    @staticmethod
    def from_dict(data: Mapping | None) -> Meta:
        raw = dict(data or {})
        return Meta(label=str(raw.get("label") or ""), total=int(raw.get("total") or 0),
                    offset=int(raw.get("offset") or 0), already_answered=int(raw.get("already_answered") or 0),
                    pasted=bool(raw.get("pasted")))


def safe(text: str | None, limit: int = MAX_QUOTE_CHARS) -> str:
    """Email text made inert for markdown: no markup characters, one line, capped."""
    flat = _SPACES.sub(" ", _UNSAFE.sub("", text or "")).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def by_bucket(threads: Sequence[Thread], bucket: str) -> list[tuple[int, Thread]]:
    return [(i, t) for i, t in enumerate(threads, start=1) if t.bucket == bucket]


def coverage_text(meta: Meta, shown: int) -> str:
    if meta.pasted:
        return f"{shown} pasted thread{'s' if shown != 1 else ''}"
    if meta.offset:
        return f"{meta.offset + 1}-{meta.offset + shown} of {meta.total} covered"
    return f"{shown} of {meta.total} covered"


def headline(threads: Sequence[Thread], meta: Meta) -> str:
    counts = {b: len(by_bucket(threads, b)) for b in ("urgent", "reply", "ignore")}
    where = "Pasted threads" if meta.pasted else f"Inbox {meta.label}".rstrip()
    return (f"{where}: {counts['urgent']} urgent, {counts['reply']} reply, {counts['ignore']} ignore "
            f"({coverage_text(meta, len(threads) + meta.already_answered)})")


def line(number: int, thread: Thread) -> str:
    parts = [f"{number}. {safe(thread.sender, 60)} - \"{safe(thread.subject, 90)}\" - {safe(thread.reason, 140)}"]
    if thread.evidence:
        parts.append(f": \"{safe(thread.evidence)}\"")
    if thread.link:
        parts.append(f" [open]({thread.link})")
    return "".join(parts)


def render_list(threads: Sequence[Thread], meta: Meta, checks: Checks, lead: str = "") -> str:
    out = [lead] if lead else []
    out.append(f"**{headline(threads, meta)}**")
    urgent = by_bucket(threads, "urgent")
    if urgent:
        out.append("Top to do first: " + ", ".join(f"#{n} {safe(t.sender, 60)}" for n, t in urgent[:TOP_N]))
    for bucket, title in (("urgent", "Urgent"), ("reply", "Reply")):
        rows = by_bucket(threads, bucket)
        out.append(f"{title}\n" + ("\n".join(line(n, t) for n, t in rows) if rows else "- none"))
    out.append(_ignore_summary(by_bucket(threads, "ignore")))
    if meta.already_answered:
        out.append(f"Already answered by you: {meta.already_answered} (not listed).")
    if meta.total > meta.offset + len(threads) + meta.already_answered and not meta.pasted:
        out.append("Say \"continue\" for the older threads.")
    out.extend(f"_Note: {n}_" for n in checks.notes)
    return "\n\n".join(out)


def _ignore_summary(rows: Sequence[tuple[int, Thread]]) -> str:
    if not rows:
        return "Ignore (0)"
    kinds: dict[str, int] = {}
    for _, t in rows:
        kind = ("bulk senders" if t.reason.startswith("bulk sender") else
                "automated notices" if t.reason.startswith("automated notice") else "no action needed")
        kinds[kind] = kinds.get(kind, 0) + 1
    detail = ", ".join(f"{k} {v}" for k, v in kinds.items())
    return f"Ignore ({len(rows)}): {detail}. Say \"show ignored\" to list them."


def render_ignored(threads: Sequence[Thread]) -> str:
    rows = by_bucket(threads, "ignore")
    if not rows:
        return "Nothing is in ignore."
    return f"**Ignored ({len(rows)})**\n\n" + "\n".join(line(n, t) for n, t in rows)


def render_why(number: int, thread: Thread) -> str:
    text = (f"#{number} {safe(thread.sender, 60)} - \"{safe(thread.subject, 90)}\" is in **{thread.bucket}**: "
            f"{safe(thread.reason, 200)}.")
    if thread.evidence:
        text += f" From the email: \"{safe(thread.evidence)}\"."
    if thread.deadline:
        text += f" Deadline read as {thread.deadline}."
    return f"{text} ({_SOURCE_TEXT.get(thread.source, thread.source)}.)"


def render_for_model(threads: Sequence[Thread]) -> str:
    """The numbered list the follow-up model sees (inside a fence)."""
    return "\n".join(f"{n}. [{t.bucket}] {safe(t.sender, 60)} - \"{safe(t.subject, 90)}\" - {safe(t.reason, 140)}"
                     for n, t in enumerate(threads, start=1))


def confidence(checks: Checks) -> str:
    if checks.pasted:
        return "low"
    if checks.demoted or checks.dropped or checks.unclassified or checks.truncated or checks.capped or checks.notes:
        return "medium"
    return "high"


def envelope(threads: Sequence[Thread], meta: Meta, checks: Checks) -> dict:
    buckets = {b: [t.public() for _, t in by_bucket(threads, b)] for b in ("urgent", "reply", "ignore")}
    uncovered = max(0, meta.total - meta.offset - len(threads) - meta.already_answered)
    return {
        "summary": headline(threads, meta),
        "items": [*buckets["urgent"], *buckets["reply"]],
        "confidence": confidence(checks),
        "needs_human": bool(buckets["urgent"]) or bool(checks.notes) or uncovered > 0,
        **buckets,
        "counts": {"urgent": len(buckets["urgent"]), "reply": len(buckets["reply"]),
                   "ignore": len(buckets["ignore"]), "already_answered": meta.already_answered,
                   "uncovered": uncovered},
        "coverage": list(checks.notes),
    }


def as_json_text(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"
