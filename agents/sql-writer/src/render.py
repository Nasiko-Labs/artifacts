"""Pure: the markdown reply, rendered by code from the envelope. Model text is cleaned first: no HTML, no images,
no links or URLs the user didn't paste, no headings. Empty sections read "None found"."""

from __future__ import annotations

import json
import re

CHECK_NAMES = {"parse": "parse", "read_only": "read-only", "schema": "schema", "dialect": "dialect",
               "projection": "same output"}
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_HEADING = re.compile(r"^#{1,6}\s*")
NONE = "None found."


def clean(text: str, allowed: str = "") -> str:
    plain = _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or "")))
    plain = _URL.sub(lambda m: m.group(0) if m.group(0) in allowed else "[link removed]", plain)
    return _HEADING.sub("", " ".join(plain.split())).strip()


def code(text: str) -> str:
    body = " ".join((text or "").split())
    return f"`` {body} ``" if "`" in body else f"`{body}`"


def sql_block(sql: str) -> str:
    return "```sql\n" + (sql or "").replace("```", "'''").strip() + "\n```"


def header(env: dict) -> str:
    how = {"named": "", "detected": " (detected)", "assumed": " (assumed)"}.get(env.get("dialect_how"), "")
    passed = [CHECK_NAMES[c] for c, ok in env["checks"].items() if ok]
    if env["mode"] == "lint":
        state = "findings only"
    elif env["validated"]:
        state = "validated: " + ", ".join(passed)
    else:
        state = f"NOT validated: the {CHECK_NAMES.get(env['failed_check'], env['failed_check'])} check failed"
    return f"**Mode: {env['mode']} · dialect: {env['dialect']}{how} · {state}**"


def render(env: dict, allowed: str = "") -> str:
    blocks = [header(env)]
    if env["mode"] != "lint":
        blocks.append(_sql_part(env))
    if env.get("explanation"):
        blocks.append("**What it does** " + clean(env["explanation"], allowed))
    if env["mode"] == "optimize" and env["changes"]:
        blocks.append("**Changes**\n" + "\n".join(f"- {c['finding_id']}: {clean(c['what'], allowed)}"
                                                    for c in env["changes"]))
    blocks.append("**Assumptions**\n" + ("\n".join(f"{n}. {clean(a, allowed)}" for n, a in
                                                   enumerate(env["assumptions"], 1)) or NONE))
    if env["mode"] != "write" or env["lint"]:
        blocks.append("**Lint**\n" + ("\n".join(_finding(f) for f in env["lint"]) or NONE))
    if env["mode"] == "optimize":
        blocks.append(_compare(env))
    if env["mode"] != "write":
        blocks.append(_indexes(env))
    notes = _notes(env)
    if notes:
        blocks.append("**Notes**\n" + "\n".join(f"- {n}" for n in notes))
    return "\n\n".join(blocks)


def _sql_part(env: dict) -> str:
    if not env["sql"]:
        return f"No query to show: {clean(env.get('failed_reason') or 'the model gave none')}."
    if env["validated"]:
        return sql_block(env["sql"])
    if env["failed_check"] == "read_only":
        return f"I won't show it: {clean(env['failed_reason'])}. I only return read queries."
    return (f"**Best attempt, not validated** ({clean(env['failed_reason'])}). Check it before running it.\n"
            + sql_block(env["sql"]))


def _finding(f: dict) -> str:
    return f"- **{f['id']} · {f['rule']} {f['title']}**: {f['note']}\n  Evidence: {code(f['evidence'])}"


def _compare(env: dict) -> str:
    if env["sql_check"]:
        return "**Compare results** (run it yourself; I can't)\n" + sql_block(env["sql_check"]) + \
            f"\n{env['sql_check_note']}"
    return f"**Compare results** {env['sql_check_note'] or NONE}"


def _indexes(env: dict) -> str:
    if not env["suggested_indexes"]:
        return f"**Suggested indexes** (not run) {env['index_note'] or NONE}"
    lines = [f"- {code(s['sql'])}: {s['reason']}" for s in env["suggested_indexes"]]
    return "**Suggested indexes** (not run)\n" + "\n".join(lines) + f"\n{env['suggested_indexes'][0]['caveat']}"


def _notes(env: dict) -> list[str]:
    notes = list(env["warnings"])
    if env["unaddressed"]:
        notes.append(f"The rewrite doesn't address {', '.join(env['unaddressed'])}.")
    if env["tables_considered"]:
        notes.append(f"Your schema is large; I considered only: {', '.join(env['tables_considered'])}.")
    if env["dropped"]:
        notes.append(f"{env['dropped']} statements from the model were dropped: they named things not in your "
                     "schema or query, or claimed speedups.")
    notes.append(f"Confidence: {env['confidence']}" + (" · needs a human check" if env["needs_human"] else ""))
    return notes


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def render_sql_only(env: dict) -> str:
    return sql_block(env["sql"]) if env.get("sql") else "There's no query in this conversation yet."
