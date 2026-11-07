"""Pure: the chat reply, rendered in code from the envelope. Every notes reply carries the "Draft" label:
nothing is ever sent. The Slack and email drafts sit in plain code blocks so they copy intact."""

from __future__ import annotations

import json
from collections.abc import Sequence

from change import Change

DRAFT_LABEL = "**Draft release notes (copy; nothing was sent)**"
FOOTER = ("_Draft: copy it. I don't send or post anything. Say \"drop item 4\", \"move item 3 to Fixed\", "
          "\"shorter Slack post\", \"friendlier\", \"show internal\" or \"as json\"._")
JSON_LABEL = "Draft: copy it; nothing was sent."
MAX_INTERNAL_SHOWN = 100


def counts_line(envelope: dict) -> str:
    included, internal = len(envelope.get("items") or []), int(envelope.get("internal_count") or 0)
    text = f"Included {included} change{'s' if included != 1 else ''}"
    if internal:
        text += f"; {internal} internal change{'s' if internal != 1 else ''} left out (show them with 'show internal')"
    return f"_({text}.)_"


def _checks(envelope: dict, unfinished: int) -> list[str]:
    items = envelope.get("items") or []
    lines = []
    titles = [str(i["number"]) for i in items if i.get("title_only")]
    if unfinished:
        lines.append(f"I ran out of time before rewriting {unfinished} item{'s' if unfinished != 1 else ''}; "
                     "they show their original titles.")
    elif titles:
        lines.append(f"Item{'s' if len(titles) != 1 else ''} {', '.join(titles)} use{'s' if len(titles) == 1 else ''} "
                     "the original title: I couldn't write a sentence that said only what the change says.")
    if any(i.get("breaking") and not i.get("migration") for i in items):
        lines.append("A breaking change has no migration steps: fill in [migration steps?] before sharing.")
    if any(i.get("block") == "security" for i in items):
        lines.append("Includes a security fix: have someone check the wording before it goes out.")
    missing = [p for p, v in (("[version?]", envelope.get("version")), ("[date?]", envelope.get("date"))) if not v]
    if missing:
        lines.append(f"Fill in {' and '.join(missing)}: I never guess them.")
    return lines


def render(envelope: dict, channel: str = "", notes: Sequence[str] = (), unfinished: int = 0,
           notes_view: str | None = None) -> str:
    slack_label = f"**Slack draft** (mrkdwn{', for ' + channel if channel else ''})"
    parts = [DRAFT_LABEL, "", notes_view or envelope["notes_markdown"]]
    if not envelope.get("items"):
        parts += ["", "_No customer-facing changes are left in these notes._"]
    parts += ["", slack_label, "```", envelope["slack_post"], "```", "", "**Email draft** (plain text)", "```",
              envelope["email_draft"], "```", "", counts_line(envelope)]
    extra = [*notes, *_checks(envelope, unfinished)]
    if extra:
        parts += ["", *(f"- {line}" for line in extra)]
    parts += ["", FOOTER]
    return "\n".join(parts)


def render_json(envelope: dict) -> str:
    return f"{JSON_LABEL}\n\n```json\n{json.dumps(envelope, ensure_ascii=False, indent=2)}\n```"


def render_internal(changes: Sequence[Change]) -> str:
    if not changes:
        return "No changes were left out as internal. _Draft: copy it; nothing was sent._"
    shown = changes[:MAX_INTERNAL_SHOWN]
    lines = [f"**Left out as internal ({len(changes)})**", ""]
    lines += [f"- {c.title}{' (' + c.ref + ')' if c.ref else ''}" + (f" [{c.prefix}]" if c.prefix else "")
              for c in shown]
    if len(changes) > len(shown):
        lines.append(f"- ...and {len(changes) - len(shown)} more")
    lines += ["", "_Say \"include internal\" to write notes with them. Draft only: nothing was sent._"]
    return "\n".join(lines)
