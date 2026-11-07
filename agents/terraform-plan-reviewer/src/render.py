"""Pure: the markdown reply, rendered in code from the envelope. No raw HTML, no images, no links; plan values
sit in code spans. Views: default, block-only, high, medium, one address, apply order, questions, evidence."""

from __future__ import annotations

import json
import re

from verdict import GRAPH_NOTE

_TAG = re.compile(r"<[^>]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_PHRASE = {"create": "will be created", "update": "will be updated in place", "delete": "will be destroyed",
           "replace": "must be replaced", "read": "will be read", "import": "will be imported", "move": "moves",
           "forget": "will no longer be managed"}
MAX_LOW_SHOWN = 10


def clean(text: str) -> str:
    return _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or ""))).strip()


def code(text: str) -> str:
    return "`" + str(text).replace("`", "'") + "`"


def counts_line(env: dict) -> str:
    c = env["counts"]
    line = (f"Plan: {c['add']} to add, {c['change']} to change, {c['destroy']} to destroy, {c['replace']} to replace"
            f" ({c['total']} resource change(s)")
    extra = [f"{c[k]} {k}" for k in ("import", "read", "move", "forget") if c.get(k)]
    if c.get("drift_notes"):
        extra.append(f"{c['drift_notes']} drift note(s)")
    return line + (", " + ", ".join(extra) if extra else "") + ")"


def finding_lines(n: int, f: dict, evidence: bool) -> list[str]:
    head = f"{n}. {code(f['address'])}" + (f" {_PHRASE[f['action']]}" if f["action"] in _PHRASE else "")
    tags = " (confirmed by you)" if f["confirmed"] else (" **(blocking)**" if f["blocking"] else "")
    lines = [f"{head}{tags}: {clean(f['reason'])} [{f['rule_id']}]"]
    if f["attribute"]:
        lines.append(f"   {code(f['attribute'])}: {code(f['before'] if f['before'] is not None else 'null')} → "
                     f"{code(f['after'] if f['after'] is not None else 'null')}")
    lines.append(f"   Ask: {clean(f['question'])}")
    if evidence and f["evidence"]:
        lines.append(f"   Evidence: {code(f['evidence'])}")
    return lines


def _section(title: str, findings: list[dict], evidence: bool) -> list[str]:
    out = [f"**{title}**"]
    if not findings:
        return [*out, "None found."]
    for n, f in enumerate(findings, start=1):
        out += finding_lines(n, f, evidence)
    return out


def apply_order_lines(env: dict) -> list[str]:
    steps = env["suggested_apply_order"]
    if not steps:
        return ["**Suggested apply order**", "Nothing to apply."]
    out = ["**Suggested apply order**"]
    for s in steps:
        out.append(f"{s['step']}. {s['tier'].capitalize()}: {', '.join(code(a) for a in s['addresses'])}. {s['note']}")
    return [*out, f"_{GRAPH_NOTE}_"]


def render(env: dict, view: str = "default", evidence: bool = False, address: str = "") -> str:
    env_note = env["environment"] + (", assumed" if env["environment_assumed"] else "")
    out = [f"**Verdict: {env['verdict'].upper()}** (environment: {env_note})", counts_line(env), "",
           clean(env["summary"]), ""]
    high = env["high_risk"]
    if view == "block":
        out += _section("Blocking changes", [f for f in high if f["blocking"] and not f["confirmed"]], evidence)
    elif view == "address":
        picked = [f for f in env["high_risk"] + env["medium_risk"] + env["low_risk"] if address in f["address"]]
        out += _section(f"Findings for {code(address)}", picked, evidence)
    elif view == "order":
        out += apply_order_lines(env)
    elif view == "questions":
        out += ["**Questions to ask the author**", *_numbered(env["questions_to_ask"])]
    else:
        out += _default_body(env, view, evidence)
    out += _footer(env)
    return "\n".join(out).strip()


def _default_body(env: dict, view: str, evidence: bool) -> list[str]:
    out: list[str] = []
    if view in ("default", "high"):
        out += _section("High risk", env["high_risk"], evidence) + [""]
    if view in ("default", "medium"):
        out += _section("Medium risk", env["medium_risk"], evidence) + [""]
    if view == "default":
        low = env["low_risk"]
        if low:
            shown = ", ".join(f"{code(f['address'])} ({clean(f['reason'])})" for f in low[:MAX_LOW_SHOWN])
            more = f" and {len(low) - MAX_LOW_SHOWN} more" if len(low) > MAX_LOW_SHOWN else ""
            out += [f"**Lower risk:** {shown}{more}", ""]
        out += ["**Questions to ask the author**", *_numbered(env["questions_to_ask"]), ""]
        out += apply_order_lines(env)
    return out


def _numbered(items: list[str]) -> list[str]:
    return [f"{n}. {clean(q)}" for n, q in enumerate(items, start=1)] or ["None."]


def _footer(env: dict) -> list[str]:
    out = [""]
    notes = [*env["caveats"], *env["notes"]]
    if env["drift"]:
        notes.append("Drift: " + "; ".join(env["drift"][:5]))
    if notes:
        out += ["**Caveats**", *(f"- {clean(n)}" for n in notes), ""]
    blocking = [f["address"] for f in env["high_risk"] if f["blocking"] and not f["confirmed"]]
    hint = f"Plan format: {env['format']}."
    if blocking:
        hint += f" To go ahead on purpose, reply \"I confirm destroying {blocking[0]}\"."
    hint += " Ask \"only the high-risk ones\", \"show evidence\", \"apply order\" or \"as json\"."
    return [*out, f"_{hint}_"]


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
