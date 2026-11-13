"""Pure: the reply text. The draft is rendered by code: markdown images and raw HTML are removed, and a
markdown link is shown as plain text with its address (which the grounding check has vetted)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from draft import Draft
from grounding import placeholders
from register import register_for
from tone import FORMALITY, Profile

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML = re.compile(r"</?[A-Za-z][^>]*>|<!--.*?-->", re.DOTALL)
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
_TONE_SOURCE = {"sample": "tone from your sample", "settings": "tone from your settings",
                "default": "neutral tone"}
DEFAULT_TONE_NOTE = "Using a neutral tone; paste a sample of your writing to match your style."
FOOTER = ("_Draft only: I don't send messages; copy it when you're happy. Say \"shorter\", \"more casual\", "
          "or \"make it a chat message\"._")


def sanitize(text: str) -> str:
    out = _IMAGE.sub("", text or "")
    out = _HTML.sub("", out)
    out = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def recipient_label(draft: Draft) -> str:
    name, role = draft.recipient_name, draft.recipient_role
    if name and role:
        return f"{name} ({role})"
    return name or role or "[recipient?]"


def draft_reply(draft: Draft, profile: Profile, notes: Sequence[str] = ()) -> str:
    reg = register_for(draft.channel)
    header = (f"**{reg.label} to {recipient_label(draft)}** · {FORMALITY[profile.formality]} {profile.formality}/5"
              f" · {_TONE_SOURCE.get(profile.source, 'neutral tone')}")
    lines = [header]
    if draft.subject:
        lines.append(f"**Subject:** {sanitize(draft.subject)}")
    lines.append("")
    lines.append(sanitize(draft.body))
    blanks = placeholders(f"{draft.subject}\n{draft.body}")
    tail = [f"Fill in before sending: {', '.join(blanks)}"] if blanks else []
    tail += list(notes)
    if profile.source == "default":
        tail.append(DEFAULT_TONE_NOTE)
    tail.append(FOOTER)
    return "\n".join(lines) + "\n\n" + "\n".join(tail)


def facts_reply(draft: Draft | None) -> str:
    if draft is None:
        return "I haven't written a draft in this conversation yet."
    if not draft.facts_used:
        return "This draft uses no specific facts from what you told me."
    lines = [f"- {fact} (from: \"{source}\")" for fact, source in draft.facts_used]
    return "Facts I used from what you told me:\n" + "\n".join(lines)


def tone_saved(profile: Profile, has_draft: bool, lead: str = "Saved your tone") -> str:
    parts = [f"{lead}: {profile.summary()}."]
    if profile.sign_off not in ("none", Profile().sign_off):
        parts.append(f"Sign-off: {profile.sign_off.replace(chr(10), ' / ')}.")
    if profile.quirks:
        parts.append("Habits: " + "; ".join(profile.quirks) + ".")
    parts.append("Say 'redo' to apply it to the current draft." if has_draft
                 else "Now tell me who to write to, what the message should do, and where you'll send it.")
    return " ".join(parts)
