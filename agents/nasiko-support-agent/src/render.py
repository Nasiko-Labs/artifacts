"""Pure: reply text and the JSON envelope. How-to = UI path → numbered steps → CLI → good to know →
source. Troubleshoot = most likely cause → fix steps → what to paste if that wasn't it → source."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from diagnose import Signature
from kb import DOCS_HOME, Section

MAX_STEPS = 7
HAND_OFF = "ask in your team's Nasiko channel or your workspace admin"
PASTE_MORE = ("If that wasn't it: paste the status line from `nasiko ps`, or the last 50 lines of "
              "`nasiko logs my-agent -n 50`, here.")
OPENRUNTIME_NOTE = "(The Deploy page calls the runtime that runs your agent OpenRuntime; it's part of Nasiko.)"
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((/[^)\s]*)\)")
_ARROW = "→"


@dataclass(frozen=True)
class Reply:
    """One rendered answer plus what its envelope reports."""

    text: str
    mode: str = "howto"                       # howto | troubleshoot | clarify | info | decline
    items: tuple[str, ...] = ()
    where: str = ""
    cli: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    confidence: str = "high"
    needs_human: bool = False
    removed: tuple[str, ...] = field(default=())   # what the fence took out


def bold_path(where: str) -> str:
    return f"**{where}**" if where else ""


def cli_line(commands: Sequence[str]) -> str:
    return "CLI: " + " · ".join(f"`{c}`" for c in commands) if commands else ""


def source_line(title: str, url: str, snapshot_date: str) -> str:
    as_of = f" (notes as of {snapshot_date}; it may have changed since)" if snapshot_date else ""
    return f"Source: {title}: {url or DOCS_HOME}{as_of}"


def docs_links(text: str) -> str:
    """Relative docs links ([x](/quickstart)) become absolute docs.nasiko.com links."""
    return _MD_LINK.sub(lambda m: f"{m.group(1)} ({DOCS_HOME}{m.group(2)})", text or "")


def numbered(steps: Sequence[str]) -> list[str]:
    return [f"{i}. {s.strip()}" for i, s in enumerate(list(steps)[:MAX_STEPS], 1) if s.strip()]


def howto(section: Section, steps: Sequence[str], cli: Sequence[str], good_to_know: str, *,
          where: str = "", snapshot_date: str = "", lead: str = "") -> Reply:
    where = where or section.where
    lines = [lead] if lead else []
    if where:
        lines.append(f"Where: {bold_path(where)}")
    lines += numbered(steps)
    if cli:
        lines.append(cli_line(cli))
    if good_to_know:
        lines.append(f"Good to know: {good_to_know.strip()}")
    if section.id.startswith("deploy-agent"):
        lines.append(OPENRUNTIME_NOTE)
    if section.edition == "ee":
        lines.append("Enterprise only: this isn't in the open-source edition.")
    lines.append(source_line(section.title, section.source_url, snapshot_date))
    return Reply("\n".join(docs_links(line) for line in lines), "howto", tuple(steps), where, tuple(cli),
                 (section.source_url,))


_CODE_FENCE_LINE = re.compile(r"^\s*```")
_THEME_ATTR = re.compile(r"\s*theme=\{null\}")


def doc_excerpt(section: Section, max_words: int = 120) -> str:
    """The chunk's body as readable text: code-fence markers and `theme={null}` removed (code lines are
    indented instead), line breaks kept, clipped to about `max_words` words."""
    body = section.text.split("\n", 1)[1] if "\n" in section.text else section.text
    lines, count, in_code = [], 0, False
    for raw in body.split("\n"):
        if _CODE_FENCE_LINE.match(raw):
            in_code = not in_code
            continue
        line = _THEME_ATTR.sub("", raw).rstrip()
        line = f"    {line}" if in_code and line else line
        words = line.split()
        if count + len(words) > max_words:
            lines.append(" ".join(words[:max_words - count]) + " …")
            break
        count += len(words)
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return docs_links(text)


def troubleshoot(signatures: Sequence[Signature], source: Section | None, snapshot_date: str) -> Reply:
    primary = signatures[0]
    lines = [f"Most likely: {primary.cause}"]
    lines += numbered(primary.fix)
    if primary.cli:
        lines.append(cli_line(primary.cli))
    for other in signatures[1:3]:
        lines.append(f"Also in what you pasted: {other.cause}")
    lines.append(PASTE_MORE)
    url = source.source_url if source else DOCS_HOME
    if source is not None:
        lines.append(source_line(source.title, url, snapshot_date))
    return Reply("\n".join(lines), "troubleshoot", primary.fix, "", primary.cli, (url,), "high",
                 primary.id in ("llm-403", "pending-no-logs"))


def not_in_notes(snapshot_date: str) -> Reply:
    text = (f"I don't have that in my Nasiko notes (as of {snapshot_date}), so I won't guess. Search "
            f"{DOCS_HOME}, or {HAND_OFF}.")
    return Reply(text, "info", confidence="low", needs_human=True, sources=(DOCS_HOME,))


def still_stuck() -> Reply:
    text = (f"Sorry, I couldn't pin it down from here. Please {HAND_OFF}, with the log lines and what you've "
            "already tried.")
    return Reply(text, "info", confidence="low", needs_human=True)


def plans_unconfirmed() -> Reply:
    text = ("Plan limits aren't in my notes yet, so I can't tell you what your plan includes or allows. "
            "Check with your workspace admin.")
    return Reply(text, "info", confidence="low", needs_human=True)


def plans_confirmed(plans: dict) -> Reply:
    lines = ["Plans, from my notes:"]
    for name, detail in plans.items():
        lines.append(f"- {name}: {detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)}")
    return Reply("\n".join(lines), "info")


def envelope(reply: Reply, notes: Sequence[str] = ()) -> dict:
    summary = next((line for line in reply.text.split("\n") if line.strip()), "")
    return {"summary": summary, "items": list(reply.items), "confidence": reply.confidence,
            "needs_human": reply.needs_human, "mode": reply.mode, "where": reply.where, "cli": list(reply.cli),
            "sources": list(reply.sources), "notes": list(notes), "fenced": list(reply.removed)}


def as_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"
