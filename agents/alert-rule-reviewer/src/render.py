"""Pure: the markdown reply, rendered by code from the stored envelope. No raw HTML, no images, no links that
are not in the paste (config only ever appears inside code spans or fenced blocks)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import catalog

MAX_SHOWN_PER_SECTION = 40
MAX_CHANGES_SHOWN = 25
LEVELS = ("high", "medium", "low")
NOISE_NOTE = ("Paste the alerts that actually fired into the Alert Noise Classifier for real noise; this review "
              "only sees how rules are written.")


@dataclass(frozen=True)
class View:
    severity: str | None = None
    rule: str | None = None
    section: str = "all"          # all | changes | noisy
    alert: str | None = None


def code(text: str) -> str:
    """An inline code span that survives backticks inside `text`."""
    text = " ".join(str(text).split())
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def block(text: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def safe(text: str) -> str:
    """Model-free, code-written prose may still carry config words: keep angle brackets out of HTML."""
    parts = re.split(r"(`[^`]*`)", text)
    return "".join(p if p.startswith("`") else p.replace("<", "&lt;").replace(">", "&gt;") for p in parts)


def _finding(n: int, f: dict) -> str:
    title = catalog.RULES[f["rule_id"]].title
    head = f"{n}. {code(f['target'])} ({f['file']}:{f['line']}) **{f['rule_id']}** {title}: {safe(f['message'])}"
    return f"{head}\n   Evidence: {code(f['evidence'])}\n   Fix: {safe(f['fix_hint'])}"


def filtered(env: dict, view: View) -> list[dict]:
    items = env.get("findings") or []
    if view.severity:
        items = [f for f in items if f["severity"] == view.severity]
    if view.rule:
        items = [f for f in items if f["rule_id"] == view.rule]
    if view.alert:
        items = [f for f in items if f["target"] == view.alert]
    return items


def _findings(env: dict, view: View) -> list[str]:
    items = filtered(env, view)
    out = []
    for level in (view.severity,) if view.severity else LEVELS:
        chosen = [f for f in items if f["severity"] == level]
        if not chosen and (view.rule or view.alert):
            continue
        out.append(f"**{level.capitalize()}**")
        shown = [_finding(n, f) for n, f in enumerate(chosen[:MAX_SHOWN_PER_SECTION], start=1)]
        out.append("\n".join(shown) if shown else "None found.")
        if len(chosen) > MAX_SHOWN_PER_SECTION:
            out.append(f"_…and {len(chosen) - MAX_SHOWN_PER_SECTION} more {level} findings (ask for one rule, e.g. "
                       "'only AR004', or 'as json' for all)._")
    if not out:
        out.append("None found.")
    return out


def _noisy(env: dict) -> str:
    rows = env.get("noisy_by_construction") or []
    body = ", ".join(f"{code(r['alert'])} ({', '.join(r['rules'])})" for r in rows) or "None found."
    return f"**Likely noisy by construction** (flap-prone by how they are written; no firing history seen): {body}"


def _changes(env: dict, view: View) -> list[str]:
    changes = env.get("proposed_changes") or []
    if view.severity or view.rule or view.alert:
        shown = {f["id"] for f in filtered(env, view)}
        changes = [c for c in changes if shown & set(c["finding_ids"])]
    out = ["**Proposed changes** (snippets with only the changed keys; fill the `<placeholders?>`)"]
    if not changes:
        return out + ["None found."]
    for n, c in enumerate(changes[:MAX_CHANGES_SHOWN], start=1):
        lang = "hcl" if c["after"].startswith("resource ") else ("json" if c["after"].startswith(("{", "[")) else "yaml")
        refs = ", ".join(c["finding_ids"])
        out.append(f"{n}. {code(c['target'])}: {c['change_type']} ({refs}). {safe(c['note'])}")
        if c["before"]:
            out.append("Before:\n" + block(c["before"], lang))
        out.append(("After:\n" if c["before"] else "Add:\n") + block(c["after"], lang))
    if len(changes) > MAX_CHANGES_SHOWN:
        out.append(f"_…and {len(changes) - MAX_CHANGES_SHOWN} more ('as json' lists all)._")
    return out


def _header(env: dict) -> str:
    counts = {level: sum(1 for f in env.get("findings") or [] if f["severity"] == level) for level in LEVELS}
    formats = ", ".join(env.get("formats") or [])
    return (f"**Reviewed {env.get('alerts_reviewed', 0)} alerts ({formats}).** {counts['high']} high · "
            f"{counts['medium']} medium · {counts['low']} low")


def _footer(env: dict) -> list[str]:
    out = []
    dropped = env.get("dropped") or {}
    if dropped.get("findings") or dropped.get("proposed_changes"):
        out.append(f"_{dropped.get('findings', 0)} finding(s) and {dropped.get('proposed_changes', 0)} proposed "
                   "change(s) were left out because they could not be checked against your paste._")
    not_checked = env.get("not_checked") or []
    if not_checked:
        out.append("_Not checked: " + safe(" ".join(not_checked)) + "_")
    out.append(f"_{NOISE_NOTE}_")
    return out


def render(env: dict, view: View | None = None) -> str:
    view = view or View()
    parts = [_header(env)]
    if view.section in ("all",):
        parts += _findings(env, view)
        if not (view.severity or view.rule or view.alert):
            parts.append(_noisy(env))
        parts += _changes(env, view)
    elif view.section == "changes":
        parts += _changes(env, view)
    elif view.section == "noisy":
        parts.append(_noisy(env))
    return "\n\n".join(parts + _footer(env))


def as_json(env: dict) -> str:
    return block(json.dumps(env, indent=2, ensure_ascii=False), "json")
