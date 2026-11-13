"""Pure: the markdown reply (or the fenced JSON envelope), built by code from the checked envelope. Markdown only:
no raw HTML, no images, and URLs only where the user's own paste had them."""

from __future__ import annotations

import json

import prompts

FENCE_LANG = {"python": "python", "javascript": "javascript", "java": "java", "go": "go", "rust": "rust",
              "ruby": "ruby", "csharp": "csharp"}
ORIGIN_TEXT = {"app": "your code", "library": "library code", "none": "unknown"}


def code(text: str) -> str:
    tick = "``" if "`" in text else "`"
    pad = " " if tick == "``" else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"


def render(envelope: dict, notes: tuple[str, ...] = ()) -> str:
    items = envelope.get("items") or []
    sections = []
    for n, item in enumerate(items):
        heading = ""
        if len(items) > 1:
            role = " (primary)" if n == 0 else ""
            heading = f"### Error {item['trace_index']} of the pasted traces{role}\n"
        sections.append(heading + render_item(item))
    footer = [*_footer(envelope), *notes, prompts.READ_ONLY_NOTE]
    return "\n\n---\n\n".join(sections) + "\n\n" + "\n".join(footer)


def render_item(item: dict) -> str:
    message = f" — {item['message']}" if item.get("message") else ""
    lines = [f"**{item['exception']}** ({item['language']}){message}"]
    if item.get("chain"):
        chain = " → ".join(code(c) for c in item["chain"])
        lines.append(f"**Chain (root cause first):** {chain}")
    lines.append(_look_at(item))
    if item.get("frame_reason"):
        lines.append(f"_Chosen over the innermost app frame: {item['frame_reason']}_")
    cause = item.get("likely_cause") or ""
    if not item.get("evidence"):
        cause += " _(unverified: no line from your paste backs this)_"
    lines.append(f"\n**Probable cause.** {cause}")
    for ev in item.get("evidence") or ():
        where = "your snippet" if ev["source"] == "code" else "the trace"
        lines.append(f"> {code(ev['quote'])}  ({where})")
    if item.get("fix_suggestion"):
        lines.append(f"\n**Fix.** {item['fix_suggestion']}")
    if item.get("fix_code"):
        lang = FENCE_LANG.get(item.get("language") or "", "")
        lines.append(f"```{lang}\n{item['fix_code'].rstrip()}\n```")
    steps = item.get("how_to_verify") or []
    if steps:
        lines.append("\n**How to verify.**")
        lines.extend(f"{i}. {step}" for i, step in enumerate(steps, start=1))
    if item.get("need_code"):
        need = item["need_code"]
        lines.append(f"\nTo be sure, paste lines {need['lines']} of {code(need['file'])}.")
    if item.get("confidence_notes"):
        lines.append(f"\n_What would change this: {item['confidence_notes']}_")
    lines.append(f"\n_Confidence: {item['confidence']}._{_checks(item.get('checks') or {})}")
    return "\n".join(lines)


def _look_at(item: dict) -> str:
    detail = item.get("location_detail")
    if not detail:
        return "**Look at:** no stack frame was found in the paste, so the location is unknown."
    where = code(f"{detail['file']}:{detail['line']}")
    func = f" in {code(detail['function'])}" if detail.get("function") else ""
    origin = ORIGIN_TEXT.get(detail["origin"], detail["origin"])
    source = "line shown from your snippet" if detail["from"] == "code" else "from the trace; no code pasted for it"
    if detail["origin"] == "library":
        return (f"**Look at:** {where}{func} ({origin}: the error surfaces inside a library; check how your code "
                "calls it)")
    return f"**Look at:** {where}{func} ({origin}; {source})"


def _checks(checks: dict) -> str:
    parts = []
    if checks.get("quotes_removed"):
        parts.append(f"removed {checks['quotes_removed']} quote(s) not found in your paste")
    if checks.get("sentences_removed"):
        parts.append(f"removed {checks['sentences_removed']} sentence(s) citing something not in your paste")
    if checks.get("fix_code_dropped"):
        parts.append("dropped a code fix that failed the checks")
    if checks.get("repairs"):
        parts.append(f"one repair pass fixed {checks.get('repaired_problems') or 0} problem(s)")
    return f" Checks: {'; '.join(parts)}." if parts else ""


def _footer(envelope: dict) -> list[str]:
    out = []
    if envelope.get("skipped_traces"):
        out.append(f"_First {envelope['skipped_traces']} trace(s) skipped: I explain the last 3 in one message._")
    if envelope.get("unexplained_traces"):
        numbers = ", ".join(str(n) for n in envelope["unexplained_traces"])
        out.append(f"_Not explained this turn (no model call left or no answer): trace {numbers}. Paste it alone._")
    return out


def render_followup(answer: str, removed: int) -> str:
    note = f"\n\n_Removed {removed} sentence(s) citing something not in your paste._" if removed else ""
    return f"{answer}{note}\n\n{prompts.READ_ONLY_NOTE}"
