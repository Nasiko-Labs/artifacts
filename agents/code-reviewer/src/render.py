"""Pure: the reply, rendered by code from the envelope. Markdown only: no raw HTML, no images, and no link that
is not in the input. Comments keep their number from the full review, so "explain comment 3" stays stable
when a view is filtered."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import prompts
from envelope import tally

VERDICT_LABEL = {"request_changes": "request changes", "comment": "comments only, nothing blocking",
                 "no_blocking_comments": "no blocking comments"}
_HTML = re.compile(r"</?[A-Za-z][^>]*>|<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def clean(text: str) -> str:
    return _HTML.sub("", _IMAGE.sub("", text or "")).strip()


def render(env: dict, severities: Sequence[str] | None = None, notes: Sequence[str] = ()) -> str:
    comments = list(enumerate(env.get("comments") or [], start=1))
    shown = [(n, c) for n, c in comments if severities is None or c["severity"] in severities]
    lines = [_headline(env)]
    if severities is not None:
        lines.append(f"_Showing {len(shown)} of {len(comments)} comments ({', '.join(severities)}). "
                     "Say \"show all\" for the full review._")
    current = None
    for n, c in shown:
        if c["file"] != current:
            current = c["file"]
            lines += ["", f"**{clean(current)}**"]
        lines.append(_comment(n, c))
    if not comments:
        lines += ["", "I found nothing worth a comment on the added lines."]
    lines += _spec(env.get("spec"))
    lines += ["", *_footer(env, notes)]
    return "\n".join(lines).strip()


def _headline(env: dict) -> str:
    size = env["size"]
    counts = tally(env.get("comments") or [])
    head = (f"**Review: {VERDICT_LABEL.get(env['verdict'], env['verdict'])}** - {counts} ({size['files']} files, "
            f"+{size['added']} -{size['removed']}, {size['class']} size).")
    return f"{head} {env['tests_note']}" if env.get("tests_note") else head


def _comment(n: int, c: dict) -> str:
    span = f"L{c['line']}" + (f"-{c['end_line']}" if c.get("end_line") else "")
    label = f"{n}. {c['severity'].capitalize()} · {c['axis']} · {span}"
    text = clean(c["comment"])
    if c.get("rule"):
        text = f"Your standards say \"{clean(c['rule'])}\". {text}"
    if c.get("pre_existing"):
        text = f"(existing code, not in this change) {text}"
    out = f"- **{label}** {text}"
    if c.get("suggestion"):
        body = c["suggestion"].replace("```", "'''")
        out += "\n  ```suggestion\n" + "\n".join(f"  {ln}" for ln in body.split("\n")) + "\n  ```"
    return out


def _spec(spec: dict | None) -> list[str]:
    if not spec or not spec.get("requirements"):
        return []
    parts = []
    for r in spec["requirements"]:
        note = f" ({clean(r['note'])})" if r.get("note") else ""
        parts.append(f"{r['id']} {r['status']}{note}")
    lines = ["", "**Spec check:** " + " · ".join(parts)]
    lines += [f"- {r['id']}: {clean(r['text'])}" for r in spec["requirements"]]
    return lines


def _footer(env: dict, notes: Sequence[str]) -> list[str]:
    tail = list(notes) + [f"_{clean(n)}_" for n in env.get("notes") or []]
    if env.get("skipped"):
        tail.append("_Skipped: " + ", ".join(clean(s) for s in env["skipped"]) + "._")
    if env.get("dropped"):
        tail.append(f"_Dropped {env['dropped']} comment(s) I could not tie to a changed line._")
    if env.get("formatting_skipped"):
        tail.append(f"_Skipped {env['formatting_skipped']} formatting comment(s); say \"nits too\" to see them._")
    if env.get("over_cap"):
        tail.append(f"_{env['over_cap']} lower-priority comment(s) left out (at most 25, 8 per file, 5 nits)._")
    tail.append(f"_Confidence: {env['confidence']}._ {prompts.READ_ONLY}")
    return tail


def render_one(number: int, comment: dict, explanation: str, fix: str | None) -> str:
    lines = [_comment(number, {**comment, "suggestion": None}), "", clean(explanation) or "(no explanation)"]
    if fix:
        lines += ["", "Suggested replacement:", "```suggestion", fix.replace("```", "'''"), "```"]
    elif comment.get("suggestion") is None:
        lines += ["", "_No line-level replacement passed my checks; the fix is structural._"]
    lines += ["", prompts.READ_ONLY]
    return "\n".join(lines)


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"
