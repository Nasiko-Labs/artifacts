"""Pure: the markdown reply (no raw HTML, no images; URLs never added) or, when JSON was asked, the envelope
as a fenced ```json block."""

from __future__ import annotations

import json
from collections import Counter

import envelope
import prompts
from affected import Affected
from analysis import Analysis, ChangeRow

MAX_CHANGES_SHOWN = 15
MAX_AFFECTED_SHOWN = 20
ACTION_WORD = {"delete": "destroy", "replace": "replace", "update": "update", "create": "create"}


def code(text: str) -> str:
    """Inline code that cannot break out (backticks swapped, angle brackets kept literal inside code)."""
    return "`" + (text or "").replace("`", "'").replace("\n", " ") + "`"


def plain(text: str) -> str:
    return (text or "").replace("<", "&lt;").replace(">", "&gt;")


def as_json(a: Analysis) -> str:
    return "```json\n" + json.dumps(envelope.build(a), indent=2) + "\n```"


def header(a: Analysis) -> str:
    counts = a.stated or {k: a.parsed.get(k, 0) for k in ("add", "change", "destroy")}
    said = ", ".join(f"{counts.get(k, 0)} to {k}" for k in ("add", "change", "destroy"))
    stated_total = sum(counts.get(k, 0) for k in ("add", "change", "destroy"))
    parsed_total = sum(a.parsed.get(k, 0) for k in ("add", "change", "destroy"))
    parsed = f" (parsed {parsed_total}/{stated_total})" if a.stated is not None else ""
    repo = f" · {plain(a.repo)}" if a.repo else ""
    return f"**Blast radius**{repo} · plan: {said}{parsed} · this plan: {plain(a.env)}"


def change_line(c: ChangeRow) -> str:
    keys = f" ({', '.join(c.keys)} changed)" if c.keys else ""
    parts = f": {' + '.join(c.parts)}" if c.parts else ""
    tag = " (tag-only)" if c.tag_only else ""
    return f"- **{c.risk_level.upper()}** {code(c.address)} {ACTION_WORD.get(c.action, c.action)}{tag}{parts}{keys}"


def affected_line(n: int, r: Affected) -> str:
    where = f"{r.path}:{r.line}" if r.line else f"{r.path} (line not read)"
    repo = "" if r.repo == "(pasted)" else f"{plain(r.repo)} "
    snippet = f" {code(r.snippet)}" if r.snippet else ""
    return (f"{n}. {r.kind} · {r.environment} · {repo}{code(where)}{snippet} · owner {plain(r.owner)} "
            f"(depends on {code(r.change)})")


def markdown(a: Analysis) -> str:
    out = [header(a)]
    if a.view:
        out.append(f"_Showing {a.view}._")
    if a.narrative:
        out.append(plain(a.narrative))
    shown = a.changes[:MAX_CHANGES_SHOWN]
    out.append("**Changes**\n" + "\n".join(change_line(c) for c in shown)
               + (f"\n- … and {len(a.changes) - len(shown)} more" if len(a.changes) > len(shown) else ""))
    out += _affected_section(a)
    if a.breaks:
        out.append("**What could break**\n" + "\n".join(f"- {code(x)}: {plain(t)}" for x, t in a.breaks))
    if a.checks:
        out.append("**Check before apply**\n" + "\n".join(f"- {plain(c)}" for c in a.checks))
    out.append(_coverage(a))
    level = envelope.confidence(a)
    review = "yes" if envelope.needs_human(a) else "no"
    out.append(f"{prompts.READ_ONLY} Confidence: {level} · needs review: {review}.")
    return "\n\n".join(out)


def _affected_section(a: Analysis) -> list[str]:
    if not a.searched:
        return []
    if not a.affected:
        return [] if not a.code_host else [_none_found(a)]
    envs = Counter(r.environment for r in a.affected)
    counts = ", ".join(f"{n} in {env}" for env, n in sorted(envs.items(), key=lambda kv: kv[0] != "prod"))
    rows = a.affected[:MAX_AFFECTED_SHOWN]
    more = f"\n… and {len(a.affected) - len(rows)} more (all in the JSON)" if len(a.affected) > len(rows) else ""
    return [f"**Affected** ({counts})\n" + "\n".join(affected_line(i, r) for i, r in enumerate(rows, 1)) + more]


def _none_found(a: Analysis) -> str:
    repos = ", ".join(plain(r) for r in (a.repo, *a.extra_repos) if r)
    return f"No references found in {repos}; that does not prove none exist."


def _coverage(a: Analysis) -> str:
    lines = list(a.notes)
    if a.searched and not a.code_host:
        pass  # the notes say why the code host was not searched
    elif a.searched and a.eligible > len(a.searched):
        lines.insert(0, f"searched {len(a.searched)} of {a.eligible} changed resources (the {len(a.searched)} "
                        "riskiest)")
    elif a.searched:
        lines.insert(0, f"searched {len(a.searched)} of {a.eligible} changed resources")
    else:
        lines.insert(0, "0 dependents searched: nothing in this plan changes or removes an existing resource beyond tags")
    return "**Coverage**: " + "; ".join(plain(x) for x in dict.fromkeys(lines)) + "."
