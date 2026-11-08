"""Pure: the reply, built by code from the envelope. Markdown only (no HTML, no images); JSON mode returns the
envelope as one fenced block."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import prompts

MAX_QUOTE_CHARS = 160


def as_json(envelope: Mapping) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def summary(envelope: Mapping) -> str:
    files = envelope.get("suspect_files") or []
    missing = envelope.get("missing_info") or []
    kind = envelope.get("issue_kind") or "unclear"
    if not files:
        return f"No suspect file found for this {kind} issue; {len(missing)} details missing from the report."
    top = files[0]
    locs = [loc for loc in envelope.get("edit_locations") or [] if loc["path"] == top["path"]]
    where = f"`{top['path']}`"
    if locs:
        where += f" (`{locs[0]['symbol']}`, lines {locs[0]['start_line']}–{locs[0]['end_line']})"
    return (f"Likely a {kind} in {where}; {len(files)} file{'s' if len(files) != 1 else ''} considered, "
            f"{len(missing)} detail{'s' if len(missing) != 1 else ''} missing from the report.")


def markdown(envelope: Mapping, coverage: Sequence[str], issue_number: int | None) -> str:
    files = envelope.get("suspect_files") or []
    kind = envelope.get("issue_kind") or "unclear"
    label = f"Issue #{issue_number}" if issue_number else "Issue"
    lead = "where this would be answered/changed" if kind in ("question", "docs") else "likely in"
    head = (f"**{label}: {kind}** — {lead} `{files[0]['path']}` ({files[0]['bucket']})." if files
            else f"**{label}: {kind}** — no suspect file found.")
    lines = [head, ""]
    lines += _ranked(files, envelope.get("edit_locations") or [], envelope.get("suspect_symbols") or [])
    missing = envelope.get("missing_info") or []
    if missing:
        asks = "; ".join(f"{m['item']} (ask: \"{m['ask']}\")" for m in missing)
        lines += ["", f"**Missing from the report:** {asks}."]
    lines += ["", f"**Next step:** {envelope.get('next_step') or ''}"]
    ref = envelope.get("ref")
    if any(loc["precision"] == "line" for loc in envelope.get("edit_locations") or []):
        lines += ["", f"Lines refer to {'`' + ref + '`' if ref else 'the files'} as read now."]
    lines += ["", f"Confidence: {envelope.get('confidence')}" +
              ("; needs a human look." if envelope.get("needs_human") else ".")]
    tail = prompts.READ_ONLY + (" Coverage: " + "; ".join(coverage) + "." if coverage else "")
    return "\n".join([*lines, "", tail])


def _ranked(files: Sequence[Mapping], locations: Sequence[Mapping], symbols: Sequence[Mapping]) -> list[str]:
    out = []
    for n, f in enumerate(files, 1):
        locs = [loc for loc in locations if loc["path"] == f["path"]]
        signals = ", ".join(f.get("signals") or []) or "model pick only"
        trunc = " (file truncated at 12k chars)" if f.get("truncated") else ""
        if not locs:
            syms = [s["name"] for s in symbols if s["path"] == f["path"]]
            named = " " + ", ".join(f"`{s}`" for s in syms) if syms else ""
            out.append(f"{n}. `{f['path']}`{named} ({f['bucket']}, {f['score']:.2f}) — {f.get('why')} "
                       f"*(signals: {signals})*{trunc}")
            continue
        first = True
        for loc in locs:
            quote = f" — `{_short(loc['quote'])}`" if loc.get("quote") else ""
            span = f"**L{loc['start_line']}–{loc['end_line']}**" if loc["precision"] == "line" \
                else f"L{loc['start_line']}–{loc['end_line']} (whole function)"
            prefix = f"{n}. `{f['path']}`" if first else "   also"
            score = f" ({f['bucket']}, {f['score']:.2f})" if first else ""
            why = f" {loc['why']}" if loc.get("why") else ""
            out.append(f"{prefix} `{loc['symbol']}` {span}{score}{quote}{why}"
                       + (f" *(signals: {signals})*{trunc}" if first else ""))
            first = False
    return out


def _short(quote: str) -> str:
    one = " ⏎ ".join(q.strip() for q in quote.split("\n") if q.strip()).replace("`", "'")
    return one if len(one) <= MAX_QUOTE_CHARS else one[:MAX_QUOTE_CHARS - 1] + "…"
