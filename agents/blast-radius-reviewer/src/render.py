"""Pure: markdown for people (or the envelope as one fenced json block), built by code from the envelope."""

from __future__ import annotations

import json

NOT_CHECKED = "callers in other repos, runtime behaviour, anything not pasted"


def _verify_line(checks: list[str]) -> str:
    return " · ".join(f"`{c}`" if c.startswith(("grep ", "rg ")) else c for c in checks)


def _place(item: dict) -> str:
    return f"`{item['file']}:{item['line']}`" if item.get("line") else f"`{item['file']}`"


def render(env: dict, notes: tuple[str, ...] = ()) -> str:
    items, cov = env["items"], env.get("coverage") or {}
    scope = "diff + your context" if cov.get("context_given") else "diff only; no repo context given"
    count = len(items)
    lead = (f"{count} place(s) outside this diff could break." if count else "Nothing found in what you pasted.")
    out = [f"**Verdict: {env['verdict_text']}** — {lead} ({scope})"]
    if env.get("one_fact"):
        out.append(f"**The one fact:** {env['one_fact']}")
    out.append("")
    for n, item in enumerate(items, start=1):
        out.append(f"{n}. **{item['severity'].capitalize()} — {item['kind']}** {_place(item)} — {item['what']} "
                   f"*(rung: {item['rung']})*")
        out.append(f"   Why: {item['why']}")
        if item.get("how_to_verify"):
            out.append(f"   Verify: {_verify_line(item['how_to_verify'])}")
        out.append(f"   Safe if: {item['safe_if']}")
    if not items and not env.get("cleared"):
        out.append("No risk pattern fired; here is what was checked: removed or changed symbols, wire fields, "
                   "migrations, env and config keys, literals, defaults, dependencies, timing, tests, "
                   "observability names, generated code and deleted files.")
    out.append("")
    out.append(_cleared(env, cov))
    out += _coverage(cov)
    out += list(notes)
    out.append("_Analysis only: nothing was run or sent. The diff and context were read as text; copy the checks "
               "you need._")
    return "\n".join(out).strip()


def _cleared(env: dict, cov: dict) -> str:
    cleared = env.get("cleared") or []
    if cleared:
        rows = "; ".join(f"`{c['token']}` ({c['kind']}): {c['reason']}" for c in cleared)
        text = f"**Cleared:** {rows}."
    elif cov.get("context_given"):
        text = "**Cleared:** none (your context did not prove any flagged place safe)."
    else:
        text = "**Cleared:** none (no context pasted to prove anything)."
    return f"{text} **Not checked:** {NOT_CHECKED}."


def _coverage(cov: dict) -> list[str]:
    lines = []
    if cov.get("census_only"):
        lines.append("Not analysed in depth: " + ", ".join(cov["census_only"]) + ".")
    if cov.get("skipped"):
        lines.append(f"Skipped {len(cov['skipped'])} file(s) over the size cap, ranked lowest risk: "
                     + ", ".join(cov["skipped"][:20]) + ".")
    if cov.get("dropped_quotes"):
        lines.append(f"{cov['dropped_quotes']} model judgement(s) failed the verbatim check and were replaced by "
                     "generic wording.")
    if cov.get("replaced_sentences"):
        lines.append(f"{cov['replaced_sentences']} sentence(s) naming a place not in your input were replaced.")
    if cov.get("unjudged"):
        lines.append(f"{cov['unjudged']} item(s) were not judged by the model (budget or a failed call); they "
                     "show the pattern only.")
    return [f"_{line}_" for line in lines]


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"
