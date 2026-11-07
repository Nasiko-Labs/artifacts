"""Pure: the markdown reply, rendered by code from the envelope. Model text is cleaned first: no HTML, no
images, no links or URLs the user didn't paste, no headings that could restyle the reply."""

from __future__ import annotations

import json
import re

MAX_SMELLS_SHOWN = 12
MAX_ASSUMPTIONS_SHOWN = 12
RULE_NAMES = {
    "S01": "write without filter", "S02": "select star", "S03": "cartesian join", "S04": "non-equi join",
    "S05": "function on filtered column", "S06": "leading wildcard", "S07": "left join defeated",
    "S08": "NOT IN subquery", "S09": "DISTINCT masks fan-out", "S10": "aggregate after fan-out",
    "S11": "no tie-breaker", "S12": "deep offset", "S13": "correlated subquery", "S14": "comparison with NULL",
    "S15": "BETWEEN on timestamps", "S16": "OR across columns", "S17": "UNION where ALL fits",
    "S18": "HAVING without aggregate", "S19": "positional ordering", "S20": "integer division",
    "S21": "non-aggregated column", "S22": "window default frame", "S23": "large IN list",
    "S24": "writes without transaction", "M": "noted by the model",
}
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_HEADING = re.compile(r"^#{1,6}\s*")


def clean(text: str, allowed: str = "") -> str:
    plain = _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or "")))
    plain = _URL.sub(lambda m: m.group(0) if m.group(0) in allowed else "[link removed]", plain)
    return _HEADING.sub("", " ".join(plain.split())).strip()


def code(text: str) -> str:
    body = " ".join((text or "").split())
    return f"`` {body} ``" if "`" in body else f"`{body}`"


def _smell(d: dict, allowed: str, many: bool) -> str:
    where = f" (statement {d['statement']})" if many else ""
    line = (f"- **{d['severity'].capitalize()} · {RULE_NAMES.get(d['id'], d['id'])}**{where}: "
            f"{clean(d['text'], allowed)}")
    if d.get("so_what"):
        line += f" {clean(d['so_what'], allowed)}"
    line += f"\n  Evidence: {code(d['evidence'])}"
    fix = clean(d.get("hint") or "", allowed) or clean(d.get("suggestion") or "", allowed)
    return line + (f"\n  Suggestion: {fix}" if fix else "")


def render(envelope: dict, allowed: str = "", notes: tuple[str, ...] = ()) -> str:
    count, many = envelope["statements"], envelope["statements"] > 1
    noun = "statement" if count == 1 else "statements"
    blocks = [f"**What it does** ({envelope['dialect_label']}, {count} {noun})"]
    blocks.append("\n".join(clean(line, allowed) for line in envelope["plain_english"].splitlines()))
    smells = envelope["smells"][:MAX_SMELLS_SHOWN]
    if smells:
        more = len(envelope["smells"]) - len(smells)
        tail = f"\n_{more} lower-severity smells not shown; say \"as json\" for all._" if more > 0 else ""
        blocks.append("**Watch out**\n" + "\n".join(_smell(d, allowed, many) for d in smells) + tail)
    else:
        blocks.append("**Watch out**\nNo smells found by the rules.")
    assumptions = envelope["assumptions"][:MAX_ASSUMPTIONS_SHOWN]
    if assumptions:
        blocks.append("**Assumes**\n" + "\n".join(
            f"- {clean(a['text'], allowed)}{' (my reading)' if a['source'] == 'model' else ''} "
            f"Evidence: {code(a['evidence'])}" for a in assumptions))
    if envelope["confirmed_by_schema"]:
        blocks.append("**Confirmed by your schema**\n" + "\n".join(
            f"- {clean(c['note'] or c['text'], allowed)}" for c in envelope["confirmed_by_schema"]))
    blocks += _footer(envelope, notes)
    return "\n\n".join(blocks)


def _footer(envelope: dict, notes: tuple[str, ...]) -> list[str]:
    lines = [f"_{n}_" for n in envelope.get("coverage") or []]
    if envelope.get("dropped_model_items"):
        lines.append(f"_{envelope['dropped_model_items']} model statements were dropped because they named "
                     "something not in your SQL or quoted it inexactly._")
    if not envelope.get("schema_supplied") and envelope["dialect"] != "unknown":
        lines.append("_No query plan or data was available: index notes say \"if indexed\" unless you paste "
                     "CREATE TABLE / CREATE INDEX statements._")
    if envelope.get("needs_human"):
        lines.append("_Review before running: " + ("at least one critical or high smell._"
                                                    if envelope["confidence"] != "low" else
                                                    "confidence is low (unparsed SQL or many statements)._"))
    lines.append("_Read-only analysis: the SQL was parsed, never run. Say \"explain only the CTE `name`\", "
                 "\"assume t.col is NOT NULL\", \"rewrite the join as ...\" or \"as json\"._")
    return lines + [n for n in notes if n]


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def render_rewrite(sql: str, explanation: str, allowed: str = "") -> str:
    return (f"**Untested rewrite** (suggestion only; nobody has run it)\n\n```sql\n{sql.strip()}\n```\n\n"
            f"{clean(explanation, allowed)}\n\n_Check it against your data before using it._")
