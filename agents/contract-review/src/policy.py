"""Split an incoming A2A message into policy (the injected playbook) and data.

The platform's injection proxy appends L1A knowledge as its OWN text part, after the user's
turn, formatted as `"## Known facts about this agent\n- fact\n- fact"` (nasiko-cloud-rs
ee/server/src/injection_proxy.rs, ee/memory/src/prompt_context.rs::format_facts,
oss/agent-proxy/src/context_part.rs::append_context_part). Org policy (L0) is appended the same
way under `"## Organization Policy"`. Part 0 is always the user's turn, so it is data even if it
contains a heading: a pasted heading must not become policy.

Everything that is not an injected policy part (the user's text, earlier turns, other memory
parts, and every file the agent reads) is untrusted data and is only ever placed inside
delimiters in the user message, never in the system prompt.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

L1A_HEADING = "## Known facts about this agent"
ORG_POLICY_HEADING = "## Organization Policy"
HISTORY_MARKER = "\n\nCurrent message: "
# A request is "instruction, blank line, contract". Shorter tails are just more instruction.
MIN_PASTED_CONTRACT_CHARS = 300
_TEXT_FILE_SUFFIXES = (".md", ".txt", ".markdown")

_PER_BREAK_REQUEST = re.compile(
    r"\b(each|every)\s+break\b|\bone\s+(break\s+)?at\s+a\s+time\b|\bbreak\s+by\s+break\b|\bper[\s-]break\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IncomingMessage:
    request: str
    history: str | None
    playbook_rules: tuple[str, ...]
    org_policy: str
    other_data: tuple[str, ...]

    @property
    def has_playbook(self) -> bool:
        return bool(self.playbook_rules)


def text_parts(message: Any) -> list[str]:
    """Every text part of an a2a-sdk 1.x (protobuf) or dict-shaped message, in order."""
    if message is None:
        return []
    parts = message.get("parts", []) if isinstance(message, dict) else getattr(message, "parts", [])
    texts = []
    for part in parts:
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", "")
        if isinstance(text, str) and text:
            texts.append(text)
    return texts


def split_message(parts: Sequence[str]) -> IncomingMessage:
    if not parts:
        return IncomingMessage("", None, (), "", ())
    history, request = split_history(parts[0])
    rules: tuple[str, ...] = ()
    org = ""
    other: list[str] = []
    # The platform appends its injected parts last, so the LAST part with a heading wins; an
    # earlier one (e.g. supplied by the caller) is treated as data, never merged into policy.
    for text in parts[1:]:
        if text.startswith(L1A_HEADING):
            rules = parse_rules(text[len(L1A_HEADING):])
        elif text.startswith(ORG_POLICY_HEADING):
            org = text.strip()
        else:
            other.append(text)
    return IncomingMessage(request.strip(), history, rules, org, tuple(other))


def split_pasted_contract(request: str) -> tuple[str, str | None]:
    """`"{instruction}\n\n{contract}"` → (instruction, contract). A short tail stays in the request."""
    instruction, sep, tail = request.partition("\n\n")
    contract = tail.strip()
    if not sep or len(contract) < MIN_PASTED_CONTRACT_CHARS:
        return request, None
    return instruction.strip(), contract


def attached_text(message: Any) -> str | None:
    """The first attached text file (a2a `raw` part with a text media type or .md/.txt name), decoded."""
    parts = message.get("parts", []) if isinstance(message, dict) else getattr(message, "parts", [])
    for part in parts or []:
        raw = part.get("raw") if isinstance(part, dict) else getattr(part, "raw", b"")
        if not raw:
            continue
        media = (part.get("media_type") if isinstance(part, dict) else getattr(part, "media_type", "")) or ""
        name = (part.get("filename") if isinstance(part, dict) else getattr(part, "filename", "")) or ""
        if media.startswith("text/") or name.lower().endswith(_TEXT_FILE_SUFFIXES):
            text = bytes(raw).decode("utf-8", errors="replace").strip()
            if text:
                return text
    return None


def split_history(text: str) -> tuple[str | None, str]:
    """`"{history}\n\nCurrent message: {query}"` (session_history.rs) → (history, query)."""
    idx = text.rfind(HISTORY_MARKER)
    if idx == -1:
        return None, text
    return text[:idx].strip() or None, text[idx + len(HISTORY_MARKER):]


def parse_rules(block: str) -> tuple[str, ...]:
    """One rule per `- ` bullet; continuation lines join the bullet above."""
    rules: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            rules.append(stripped[2:].strip())
        elif rules:
            rules[-1] = f"{rules[-1]} {stripped}"
    return tuple(rule for rule in rules if rule)


def wants_per_break(request: str) -> bool:
    return bool(_PER_BREAK_REQUEST.search(request))


def untrusted(tag: str, text: str) -> str:
    """Wrap data in a delimiter the data itself cannot close."""
    neutral = re.sub(rf"<\s*/?\s*{re.escape(tag)}\s*>", "[removed delimiter]", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{neutral}\n</{tag}>"
