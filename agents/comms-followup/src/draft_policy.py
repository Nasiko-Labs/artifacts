"""Output policy for anything the agent shows or writes: no pricing concessions.

Playbook rule 5 — a discount needs VP approval before it is offered, and it
never goes into an email. The prompt says so; this check makes it hold even
when the model forgets or fetched text talks it into one. Applied to the draft
at `request_approval` (so nothing with a discount can be approved, and the
send guard only lets the approved text through) and to the `finish_task` text.

Pure: decisions only, no I/O.
"""

import re

_PRICING_PATTERNS = (
    re.compile(r"\bdiscount\w*", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s*%\s*off\b", re.IGNORECASE),
    re.compile(r"\bpric(?:e|ing)\s+(?:concessions?|reductions?|cuts?|breaks?|match\w*)\b", re.IGNORECASE),
    re.compile(r"\b(?:rebates?|special pricing|lower (?:the )?price)\b", re.IGNORECASE),
)


def pricing_mentions(text: str) -> list[str]:
    """The pricing-concession phrases in `text`, in order; empty when clean."""
    found = [(m.start(), m.group(0)) for p in _PRICING_PATTERNS for m in p.finditer(text)]
    return [phrase for _, phrase in sorted(found)]


def refusal(mentions: list[str]) -> str:
    quoted = ", ".join(f"'{m}'" for m in mentions[:3])
    return (
        f"Refused: the text mentions a discount or pricing concession ({quoted}). Drafts must never "
        "mention discounts, price reductions or pricing concessions — those need separate approval. "
        "Rewrite without it, and do not mention that you left it out."
    )
