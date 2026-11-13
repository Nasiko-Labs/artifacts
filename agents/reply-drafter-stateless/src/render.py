"""Pure: the reply text. Rendered by code: markdown images and raw HTML are removed, and a markdown link is
shown as plain text with its address (which the grounding check has vetted)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from style import Metrics

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML = re.compile(r"</?[A-Za-z][^>]*>|<!--.*?-->", re.DOTALL)
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
FOOTER = ("_Draft only. I don't send messages; copy it when you're happy. Say \"shorter\", \"warmer\" or "
          "\"say no instead\"._")
EXAMPLES_HINT = "Paste 1-3 of your past replies and I'll match your style."


def sanitize(text: str | None) -> str:
    out = _IMAGE.sub("", text or "")
    out = _HTML.sub("", out)
    out = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def reply_text(envelope: dict, metrics: Metrics, notes: Sequence[str] = ()) -> str:
    who = envelope.get("reply_to") or "the sender"
    label = envelope["channel"]
    plural = "s" if metrics.examples != 1 else ""
    source = f"tone from your {metrics.examples} example{plural}" if metrics.measured else "neutral default tone"
    lines = [f"**Reply to {who} ({label})** · {source}"]
    if envelope.get("subject"):
        lines.append(f"**Subject:** {sanitize(envelope['subject'])}")
    lines += ["", sanitize(envelope["draft"]), ""]
    if envelope.get("alt_shorter"):
        lines.append(f"**Shorter:** {sanitize(envelope['alt_shorter'])}")
    lines.append(f"**Tone notes:** {envelope['tone_notes']}")
    tail = []
    if envelope["placeholders"]:
        tail.append(f"Fill in before sending: {', '.join(envelope['placeholders'])}")
    asks = envelope.get("asks_unaddressed") or []
    if asks:
        tail.append("They also asked: " + "; ".join(f"\"{a['quote']}\"" for a in asks))
    tail += list(notes)
    tail.append(FOOTER)
    return "\n".join(lines) + "\n\n" + "\n".join(tail)


def tone_saved(metrics: Metrics, has_draft: bool) -> str:
    plural = "s" if metrics.examples != 1 else ""
    lead = (f"Got your style from {metrics.examples} example{plural}: about {metrics.avg_words} words, "
            f"formality {metrics.formality}/5 (approx.)")
    if metrics.greeting != "none":
        lead += f", opens with '{metrics.greeting}'"
    if metrics.sign_off != "none":
        lead += f", signs '{metrics.sign_off.replace(chr(10), ' ')}'"
    follow = ("Say 'redo' to rewrite the current reply in it." if has_draft
              else "Now paste the message you're replying to and tell me what to say back.")
    return f"{lead}. {follow}"
