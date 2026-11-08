"""Pure: the markdown reply, rendered in code from the envelope. No images, no raw HTML; the only URLs are
the stored items' own. Modes: full, short (no why-now / risks), risks of one theme, one theme in depth."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

import coverage
import prompts

QUOTE_CHARS = 140
_MD_SPECIAL = re.compile(r"([\[\]()*_`<>#|])")


def esc(text: object) -> str:
    """Model or source text as inert markdown: no links, emphasis, HTML or headings of its own."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text or "").split()))


def _link(text: str, url: str) -> str:
    return f"[{text}]({url})" if url.startswith(("https://", "http://")) else text


def _evidence(e: Mapping) -> str:
    quote = e["quote"] if len(e["quote"]) <= QUOTE_CHARS else e["quote"][:QUOTE_CHARS].rstrip() + "…"
    return f"{_link('[' + e['id'] + ']', e.get('url') or '')} \"{esc(quote)}\" ({esc(e['metric'])})"


def header(env: Mapping) -> list[str]:
    lead = (f"**Opportunity brief: {esc(env['topic'])}** · last {env['window_days']} days · "
            f"{env.get('items_read', 0)} items · {coverage.sources_label(env['sources_read'])}")
    lines = [lead]
    lines += list(env.get("sources_missing") or ())  # code-authored coverage lines
    return lines


def theme(n: int, t: Mapping, short: bool = False) -> str:
    lines = [f"### {n}. {esc(t['label'])}  ({t['strength']} · {t['kind']})",
             f"**Opportunity:** {esc(t['opportunity']) or 'unknown'}"]
    if not short:
        lines.append(f"**Why now:** {esc(t['why_now']) or 'unknown'}")
        lines.append(f"**Risks:** {'; '.join(esc(r) for r in t['risks']) or 'unknown'}")
    lines.append(f"**Next step:** {esc(t['next_step']) or 'unknown'}")
    lines.append("**Evidence:** " + " · ".join(_evidence(e) for e in t["evidence"]))
    return "\n".join(lines)


def full(env: Mapping, short: bool = False) -> str:
    parts = ["  \n".join(header(env))]
    parts += [theme(n, t, short) for n, t in enumerate(env["items"], start=1)]
    if env.get("weak_signals"):
        parts.append("Weak signals (fewer than 2 verified sources): " + "; ".join(esc(w) for w in env["weak_signals"]))
    parts.append(prompts.DISCLAIMER)
    return "\n\n".join(parts)


def risks(env: Mapping, n: int) -> str:
    t = env["items"][n - 1]
    lines = [f"**Risks of theme {n}: {esc(t['label'])}** ({t['strength']})"]
    lines += [f"- {esc(r)}" for r in t["risks"]] or ["- unknown"]
    lines.append("**Evidence:** " + " · ".join(_evidence(e) for e in t["evidence"]))
    return "\n".join(lines)


def drill(env: Mapping, n: int, t: Mapping) -> str:
    return "\n\n".join([f"**Theme {n} in depth** · {esc(env['topic'])} · last {env['window_days']} days",
                        theme(n, t), prompts.DISCLAIMER])


def as_json(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def no_signal(topic: str, days: int, notes: Sequence[str]) -> str:
    lines = [prompts.NO_SIGNAL.format(topic=esc(topic), days=days)]
    lines += list(notes)
    lines.append("Try a broader topic, a longer window (\"last 90 days\"), or paste a few items from your feed.")
    return "\n\n".join(lines)
