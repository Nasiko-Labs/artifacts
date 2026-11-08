"""Pure: the markdown reply, rendered in code from the envelope (no raw HTML, no images; pasted text is
neutralised for markdown; links only from tool output). The JSON view is the envelope in a fenced block."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import mapping

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SAFE_URL = re.compile(r"^https?://[^\s()<>\[\]]+$")


def safe(text: Any) -> str:
    """Pasted or tool text inside markdown: one line, no tags, no image syntax, no code ticks."""
    out = " ".join(str(text).split()).replace("<", "‹").replace(">", "›").replace("![", "! [")
    return out.replace("`", "'")


def safe_block(text: str) -> str:
    return "\n".join(safe(line) for line in str(text).splitlines())


def _plain(text: Any) -> str:
    return safe(text).casefold().rstrip(" .!")


def numbers_ok(text: str, source: str) -> bool:
    """Every number in `text` appears in `source`."""
    return all(n in source for n in _NUMBER.findall(text or ""))


def _issue(m: Mapping[str, Any]) -> str:
    ident = safe(m["id"])
    url = str(m.get("url") or "")
    label = f"[{ident}]({url})" if _SAFE_URL.match(url) else ident
    shipped = "; we already have this" if mapping.is_closed(m.get("state") or "") else ""
    return f'{label} "{safe(m["title"])}" ({safe(m.get("state") or "unknown")}{shipped})'


def change_line(n: int, c: Mapping[str, Any], roadmap_checked: bool) -> list[str]:
    when = f" ({c['date']})" if c.get("date") else ""
    head = f"{n}. **{safe(c['competitor'])}**, {c['category']}: {safe(c['what'])}{when}"
    matches = c.get("roadmap_matches") or []
    if matches:
        head += " - affects " + "; ".join(_issue(m) for m in matches)
    elif roadmap_checked:
        head += " - no roadmap item"
    lines = [head]
    quote = c.get("quote")
    if quote and c.get("price_fact") is None and _plain(quote) != _plain(c["what"]):
        lines.append(f'   > "{safe(quote)}"')
    return lines


def _header(env: Mapping[str, Any]) -> str:
    checks = env["checks"]
    n = len(env["competitors"])
    word = "competitor" if n == 1 else "competitors"
    return (f"**Competitor changes** ({n} {word}, {checks['entries']} changelog entries read, "
            f"{len(env['changes'])} changes found):")


def _no_baseline(env: Mapping[str, Any]) -> str | None:
    rows = env.get("no_baseline") or []
    if not rows:
        return None
    total = env["checks"].get("unbaselined", len(rows))
    shown = "\n".join(f"- {safe(r['competitor'])}: {safe(r['what'])}" for r in rows)
    more = f"\n- and {total - len(rows)} more" if total > len(rows) else ""
    return f"**Not baseline-able** ({total} undated {'entry' if total == 1 else 'entries'}, listed once, not counted as changes):\n{shown}{more}"


def markdown(env: Mapping[str, Any]) -> str:
    road = env["roadmap"]
    sections = [_header(env)]
    lines = [ln for n, c in enumerate(env["changes"], start=1) for ln in change_line(n, c, road["checked"])]
    sections.append("\n".join(lines) if lines else "No new changes in what you pasted.")
    sections.append(_no_baseline(env))
    notes = [road["line"], *env["caveats"]]
    sections.append("**Notes**\n" + "\n".join(f"- {safe(x)}" for x in notes))
    attention = "; a person should check these results." if env["needs_human"] else "."
    sections.append(f"Confidence: {env['confidence']}{attention}")
    return "\n\n".join(s for s in sections if s)


def as_json(env: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(env, indent=1, ensure_ascii=False) + "\n```"
