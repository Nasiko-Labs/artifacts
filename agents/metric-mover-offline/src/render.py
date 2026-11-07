"""Pure: the reply, in fixed sections, from the checked envelope only. Re-render views cost no LLM call.
Markdown only: no raw HTML, no images, links only when their URL is in the input.

Views: `default`, `top:<n>`, `dim:<dimension>` (that dimension's segment table and the hypotheses citing it),
`numbers` (the evidence list and every dimension's table)."""

from __future__ import annotations

import json
from collections.abc import Sequence

import htypes
import prompts
from fmt import fmt, pct, pp, rate, signed
from text import cell, clean_text

EMPTY = "None found."


def render(envelope: dict, view: str = "default", source: str = "", extra_notes: Sequence[str] = ()) -> str:
    hyps = list(envelope.get("hypotheses") or [])
    if view.startswith("top:"):
        body = [_headline(envelope), _hypotheses(hyps[:int(view.split(":", 1)[1])], source)]
    elif view.startswith("dim:"):
        body = _dimension_view(envelope, view.split(":", 1)[1], hyps, source)
    elif view == "numbers":
        body = [_headline(envelope), _evidence(envelope), *(_table(envelope, d) for d in _dims(envelope))]
    else:
        body = [_headline(envelope), _hypotheses(hyps, source), _ranking(envelope), _events(envelope, source),
                _caveats(envelope)]
    return "\n\n".join(p for p in (*body, footer(envelope, extra_notes)) if p)


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False, default=str) + "\n```"


def _dims(envelope: dict) -> list[dict]:
    return [*(envelope.get("dimension_ranking") or []), *([envelope["cross"]] if envelope.get("cross") else [])]


def _headline(envelope: dict) -> str:
    return f"**{clean_text(envelope.get('summary', ''))}**"


def _hypotheses(hyps: Sequence[dict], source: str) -> str:
    lines = []
    for h in hyps:
        evidence = "; ".join(clean_text(e, source) for e in h.get("evidence") or [])
        lines.append(f"{h['rank']}. {htypes.label(h['type'])} (score {h['score']:.2f}). Hypothesis: "
                     f"{clean_text(h['hypothesis'], source)}\n   Evidence: {evidence or 'none'}\n"
                     f"   To confirm: {clean_text(h['how_to_confirm'], source)}")
    return "**Hypotheses (ranked)**\n" + ("\n".join(lines) or EMPTY)


def _ranking(envelope: dict) -> str:
    dims = envelope.get("dimension_ranking") or []
    parts = [f"{cell(d['dimension'])} ({d['concentration']}, top 3 explain {pct(d['explained_top3'] * 100)}"
             + (f", {d['driver']})" if d.get("driver") else ")") for d in dims]
    return "**Dimensions (most concentrated first):** " + ("; ".join(parts) or EMPTY)


def _events(envelope: dict, source: str) -> str:
    used = envelope.get("events_used") or []
    if not used:
        return ""
    return "**Events used:** " + "; ".join(f"{e['id']} {clean_text(e['text'], source)}" for e in used)


def _caveats(envelope: dict) -> str:
    lines = [f"- {clean_text(c)}" for c in envelope.get("caveats") or []]
    return "**Caveats**\n" + ("\n".join(lines) or EMPTY)


def _evidence(envelope: dict) -> str:
    lines = [f"- {e['id']}: {clean_text(e['text'])}" for e in envelope.get("evidence") or []]
    return "**Evidence (computed in code)**\n" + ("\n".join(lines) or EMPTY)


def _dimension_view(envelope: dict, name: str, hyps: Sequence[dict], source: str) -> list[str]:
    match = next((d for d in _dims(envelope) if d["dimension"].casefold() == name.casefold()), None)
    if match is None:
        known = ", ".join(d["dimension"] for d in _dims(envelope)) or "none"
        return [f"There is no dimension {cell(name)} in this table (dimensions: {cell(known)})."]
    cited = {e["id"] for e in envelope.get("evidence") or [] if e.get("dimension") == match["dimension"]}
    mine = [h for h in hyps if cited & set(h.get("evidence_ids") or [])]
    return [_headline(envelope), _table(envelope, match), _hypotheses(mine, source)]


def _table(envelope: dict, d: dict) -> str:
    is_rate = envelope.get("metric_type") == "rate"
    rows = []
    for s in d.get("segments") or []:
        share = pct(s["share_pct"]) if s.get("share_pct") is not None else "n/a"
        if is_rate:
            rows.append(f"| {cell(s['segment'])} | {rate(s['before'])} | {rate(s['after'])} | "
                        f"{pct(s['weight_before'] * 100)} → {pct(s['weight_after'] * 100)} | {pp(s['mix'])} | "
                        f"{pp(s['rate_effect'])} | {share} | {s['significance']} |")
        else:
            rows.append(f"| {cell(s['segment'])} | {fmt(s['before'])} | {fmt(s['after'])} | {signed(s['effect'])} | "
                        f"{share} |")
    head = ("| Segment | Rate before | Rate after | Volume share | Mix | Rate | Share of change | Significance |\n"
            "|---|---|---|---|---|---|---|---|" if is_rate else
            "| Segment | Before | After | Change | Share of change |\n|---|---|---|---|---|")
    return f"**By {cell(d['dimension'])}** (largest movers first)\n" + (head + "\n" + "\n".join(rows) if rows
                                                                        else EMPTY)


def footer(envelope: dict, extra_notes: Sequence[str] = ()) -> str:
    meta = [prompts.FOOTER]
    if envelope.get("metric_type") == "rate":
        meta.append(prompts.RATE_METHOD)
    if envelope.get("dropped"):
        noun = "hypothesis" if envelope["dropped"] == 1 else "hypotheses"
        meta.append(f"{envelope['dropped']} model {noun} dropped by my checks.")
    meta.append(f"Confidence: {envelope.get('confidence', 'low')}.")
    if envelope.get("needs_human"):
        meta.append("Worth a human look before you rely on it.")
    notes = [n for n in (*(envelope.get("notes") or []), *extra_notes) if n]
    return "\n".join([f"_({' '.join(meta)})_", *notes])
