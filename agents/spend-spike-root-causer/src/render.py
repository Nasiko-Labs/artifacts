"""Pure: the markdown reply, the checked facts handed to the model, and the output envelope. Every number comes
from code (records.money / percent); names, titles and links only from the export or checked tool output.
Markdown only: no raw HTML, no images."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta

import aggregate as agg
import checks
import correlate
import detect
import prompts
from analysis import Analysis
from chain import Chain, ChainLink
from records import Findings, day_label, money, percent, signed_money, signed_percent

_ONSET_KIND = {"step": "clear step", "one_day": "one-day jump, not sustained", "gradual": "gradual rise, no clear step"}
_KIND_WORD = {"pr": "PR", "release": "release", "run": "workflow run", "commit": "commit"}
_KIND_VERB = {"pr": "merged", "release": "published", "run": "ran", "commit": "committed"}


def candidates(a: Analysis, f: Findings) -> tuple[correlate.Candidate, ...]:
    spike, ch = a.spike, a.chain
    if spike is None or ch is None:
        return ()
    words = correlate.match_words(ch.service, ch.dimension.value if ch.dimension else None)
    return correlate.rank(f.changes, spike.onset.day, words)


def service_line(a: Analysis) -> str | None:
    spike, ch = a.spike, a.chain
    if spike is None or ch is None or ch.service is None:
        return None
    rows = [r for r in a.rows if r.service == ch.service]
    base = agg.mean_over(rows, spike.periods.baseline)
    win = agg.mean_over(rows, spike.periods.window)
    pct = (win - base) / base if base > 0 else None
    share = ch.links[0].share
    return (f"Top service: {ch.service} {signed_percent(pct)} ({money(base)} -> {money(win)} per day), "
            f"{percent(share)} of the increase")


def spike_line(a: Analysis) -> str:
    s = a.spike
    assert s is not None
    return (f"Spend spike ({a.currency}): {signed_percent(s.delta_pct)} ({money(s.base_daily)} -> {money(s.win_daily)} "
            f"per day, last {a.window_days} days vs the {len(s.periods.baseline)} before), "
            f"{signed_money(s.delta_daily)}/day, {money(s.monthly_impact)}/month if sustained, onset "
            f"{day_label(s.onset.day)} ({_ONSET_KIND[s.onset.kind]})")


def chain_line(ch: Chain, repos: Sequence[str]) -> str:
    parts = [_link_text(link) for link in ch.links]
    if ch.team and repos:
        parts.append("repo " + ", ".join(repos))
    return "Chain: " + " -> ".join(parts)


def _link_text(link: ChainLink) -> str:
    if link.level == "service":
        return f"service {link.value}"
    if link.level == "dimension":
        return f"{link.name} {link.value} ({percent(link.share)})"
    if link.level == "tag":
        return f"{link.name}={link.value} ({percent(link.share)})"
    return f"untagged ({link.value})"


def candidates_line(a: Analysis, f: Findings) -> str | None:
    s = a.spike
    if s is None or a.chain is None or not f.code_checked:
        return None
    ranked = candidates(a, f)
    if not ranked:
        since, until = s.onset.day - timedelta(days=correlate.MAX_LAG_DAYS), s.onset.day + timedelta(days=correlate.LOOKAHEAD_DAYS)
        return (f"No deploy found in {', '.join(f.repos)} between {day_label(since)} and {day_label(until)} "
                "(merged PRs, releases, workflow runs).")
    shown = ", ".join(_candidate_text(c) for c in ranked)
    return f"Candidates (coincident with the onset, not proven): {shown}"


def _candidate_text(c: correlate.Candidate) -> str:
    ch = c.change
    title = f' "{ch.title}"' if ch.title else ""
    return (f"{_KIND_WORD[ch.kind]}{title} {_KIND_VERB[ch.kind]} {day_label(ch.day)} "
            f"in {ch.repo} ({ch.link})")


def owner_line(a: Analysis, f: Findings) -> str | None:
    if a.spike is None or a.chain is None:
        return None
    o = f.owner
    if o.source == "tag":
        return f"Owner: {o.label} (tag {a.chain.tag_key}={a.chain.team} -> chat user {o.handle or o.chat_id})."
    if o.source == "author":
        return f"Owner: {o.label} (author of the top candidate change -> chat user {o.handle or o.chat_id})."
    if a.chain.stopped_untagged:
        return "Owner: unknown (the increase is mostly untagged; add a `team` tag so it can be traced)."
    return "Owner: unknown (no matching chat user found). The draft is addressed to [owner]."


def totals_line(a: Analysis) -> str:
    e = a.export
    totals = "; ".join(f"{cur} {money(v)}" for cur, v in sorted(e.totals().items()))
    credits = e.credits()
    credit_text = ("; credits: " + "; ".join(f"{cur} {money(v)}" for cur, v in sorted(credits.items()))
                   if credits else "")
    currency = "" if e.currency_given else f" Currency {a.currency} (assumed: no currency column)."
    bad = f", {e.bad_rows} bad row(s) skipped" if e.bad_rows else ""
    others = (f" Only {a.currency} rows are analysed; {', '.join(a.other_currencies)} rows are not converted."
              if a.other_currencies else "")
    cut = (f" Your input was {e.total_chars:,} characters; I read the first {e.read_chars:,}. For the rest, export "
           "a coarser file (daily, grouped by service and one tag) and paste it again." if e.cut else "")
    return f"Read {len(e.rows)} of {e.data_rows} rows{bad}. Totals: {totals}{credit_text}.{currency}{others}{cut}"


def movers_lines(a: Analysis) -> list[str]:
    return [f"- {m.service}: {signed_money(m.delta_daily)}/day" for m in a.movers]


def markdown(a: Analysis, f: Findings, extra_notes: Sequence[str] = ()) -> str:
    lines: list[str] = []
    result = a.result
    if isinstance(result, detect.ShortHistory):
        lines += [prompts.not_enough_history(result.baseline_days), *movers_lines(a)]
    elif a.spike is None:
        lines.append(prompts.no_spike_found(a.window_days))
        if a.movers:
            lines += ["What rose most anyway:", *movers_lines(a)]
    else:
        lines += _spike_lines(a, f)
    if a.spike is not None and a.spike.over_budget:
        lines.append(f"Projected month at the window's rate: {money(a.spike.projected_month)}, over your budget of "
                     f"{money(a.spike.budget_micros or 0)}.")
    lines.append(totals_line(a))
    notes = [*f.notes, *extra_notes]
    if notes:
        lines += ["", *(f"Note: {n}" for n in dict.fromkeys(notes))]
    return "\n".join(lines)


def _spike_lines(a: Analysis, f: Findings) -> list[str]:
    ch = a.chain
    assert ch is not None
    lines = [spike_line(a)]
    svc = service_line(a)
    lines += [svc] if svc else []
    lines.append(chain_line(ch, f.repos))
    if ch.others:
        lines.append("Also rising: " + ", ".join(f"{o.value} ({percent(o.share)} of the increase)" for o in ch.others))
    cand = candidates_line(a, f)
    lines += [cand] if cand else []
    own = owner_line(a, f)
    lines += [own] if own else []
    if f.draft:
        lines += ["", "Draft message to the owner:", *(f"> {x}" if x else ">" for x in f.draft.split("\n")), "",
                  'Say "post it" to send it to the owner, or "post it to #channel".']
    return lines


def facts(a: Analysis, f: Findings) -> dict:
    """The exact values the draft and follow-up models may use; nothing else."""
    s, ch = a.spike, a.chain
    if s is None or ch is None:
        return {"spike": None}
    return {
        "spike": {"currency": a.currency, "window": f"last {a.window_days} days",
                  "base_daily": money(s.base_daily), "window_daily": money(s.win_daily),
                  "delta_daily": signed_money(s.delta_daily), "delta_pct": signed_percent(s.delta_pct),
                  "monthly_impact_if_sustained": money(s.monthly_impact), "onset": day_label(s.onset.day),
                  "onset_date": s.onset.day.isoformat(), "onset_kind": _ONSET_KIND[s.onset.kind]},
        "top_service": service_line(a),
        "chain": chain_line(ch, f.repos),
        "candidate_changes_coincident_not_proven": [
            {"title": c.change.title, "kind": _KIND_WORD[c.change.kind], "repo": c.change.repo,
             "date": day_label(c.change.day), "link": c.change.link} for c in candidates(a, f)],
        "owner": f.owner.label if f.owner.known else "[owner]",
    }


def envelope(a: Analysis, f: Findings, summary: str) -> dict:
    s, ch = a.spike, a.chain
    conf, needs = checks.confidence(a, f, candidates(a, f)), checks.needs_human(a, f)
    spike = None if s is None or ch is None else {
        "service": ch.service, "window": f"{s.periods.window[0].isoformat()}..{s.periods.window[-1].isoformat()}",
        "base_daily": money(s.base_daily), "window_daily": money(s.win_daily),
        "delta_pct": percent(s.delta_pct), "monthly_impact": money(s.monthly_impact),
        "onset": s.onset.day.isoformat(), "onset_kind": s.onset.kind, "currency": a.currency}
    chain_items = [{"level": x.level, "name": x.name, "value": x.value, "delta_daily": money(x.delta_daily),
                    "share": percent(x.share), "evidence": x.evidence} for x in (ch.links if ch else ())]
    return {
        "summary": summary, "items": chain_items, "confidence": conf, "needs_human": needs, "spike": spike,
        "chain": chain_items,
        "owner": {"display_name": f.owner.display_name, "handle": f.owner.handle, "source": f.owner.source},
        "draft": f.draft or None,
        "changes": [{**c.change.to_dict(), "lag_days": c.lag_days, "score": c.score} for c in candidates(a, f)],
        "bad_rows": a.export.bad_rows,
        "movers": [{"service": m.service, "delta_daily": money(m.delta_daily)} for m in a.movers],
        "notes": list(f.notes),
    }


def as_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=1) + "\n```"


def draft_reply(body: str) -> str:
    quoted = "\n".join(f"> {x}" if x else ">" for x in body.split("\n"))
    return f"Draft message to the owner:\n{quoted}\n\nSay \"post it\" to send it to the owner, or \"post it to #channel\"."


def copyable(target: str, body: str, reason: str) -> str:
    quoted = "\n".join(f"> {x}" if x else ">" for x in body.split("\n"))
    return f"{reason}\n\nTo: {target}\n{quoted}"
