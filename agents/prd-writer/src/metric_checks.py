"""Pure: a metric's values checked against the brief. Values must be grounded (numbers in any equivalent form);
baseline and target are assigned from the brief's cue words (`roles.py`); the window comes from the metric's basis
(`window.py`); a metric fact the model left out, with a stated target, becomes a metric. `ctx` is checks._Ctx."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from grounding import canonical, has_placeholder, normalise, tokens, ungrounded
from prd import BASELINE_PH, LIMITS, TARGET_PH, WINDOW_PH, Metric
from roles import roles_in
from window import window_in

_LABEL = re.compile(r"^(?:success(?: metrics?)?|metrics?|kpis?|goal|target)\s*:\s*", re.IGNORECASE)


def checked_metrics(mets: Sequence[Metric], ctx: Any) -> tuple[tuple, int]:
    """(the metrics with grounded values, roles and windows, plus any left-out metric fact, the number dropped)."""
    kept = []
    for m in mets:
        if ungrounded(m.name, ctx.norm) or ungrounded(m.definition, ctx.norm):
            continue
        if m.proposed:
            kept.append(replace(m, baseline=BASELINE_PH, target=TARGET_PH, window=WINDOW_PH))
            continue
        baseline, target = _roles(m, _value(m.baseline, BASELINE_PH, ctx), _value(m.target, TARGET_PH, ctx), ctx)
        kept.append(replace(m, baseline=baseline, target=target, window=_window(m, ctx)))
    return (*kept, *_left_out_metrics(kept, ctx)), len(mets) - len(kept)


def _left_out_metrics(kept: Sequence[Metric], ctx: Any) -> tuple[Metric, ...]:
    """A metric fact no metric cites, whose brief sentence states a target ("Success: 30% ... in month one"),
    becomes a metric, so a stated success measure is never left as "None stated"."""
    cited = {b for m in kept for b in m.basis}
    out = []
    for fact in ctx.metric_facts:
        if fact.id in cited:
            continue
        draft = Metric(name=_LABEL.sub("", " ".join(fact.text.split())).rstrip("."), basis=(fact.id,))
        baseline, target = _roles(draft, BASELINE_PH, TARGET_PH, ctx)
        if target != TARGET_PH and not any(_same(m.target, target) for m in (*kept, *out)):  # no duplicates
            out.append(replace(draft, baseline=baseline, target=target, window=_window(draft, ctx)))
    return tuple(out)[:max(0, LIMITS["metrics"] - len(kept))]


def _roles(metric: Metric, baseline: str, target: str, ctx: Any) -> tuple[str, str]:
    """(baseline, target) as the brief's cue words assign them in the sentence holding the metric's basis
    ("from 140 to 40", "Success: 30% ..."); the model's assignment stands only where the brief has no cue."""
    snippets = [normalise(ctx.snippets.get(b, "")) for b in metric.basis]
    for sentence in ctx.sentences:
        norm = normalise(sentence)
        if not any(s and (s in norm or norm in s) for s in snippets):
            continue
        said_baseline, said_target = roles_in(sentence)
        if said_baseline is None and said_target is None:
            continue
        if said_baseline is None and _same(baseline, said_target):
            baseline = BASELINE_PH  # the success number was put in the baseline column
        return said_baseline or baseline, said_target or (TARGET_PH if _same(target, said_baseline) else target)
    return baseline, target


def _same(value: str, number: str | None) -> bool:
    return number is not None and canonical(value) is not None and canonical(value) == canonical(number)


def _window(metric: Metric, ctx: Any) -> str:
    """A window with grounded numbers or dates stays; else the window its basis facts state ("in month one");
    else the model's words when the brief contains them verbatim; else the placeholder."""
    kept = _value(metric.window, WINDOW_PH, ctx)
    if kept != WINDOW_PH:
        return kept
    stated = next((w for w in (window_in(ctx.snippets.get(b, "")) for b in metric.basis) if w), None)
    if stated:
        return stated
    words = normalise(metric.window).removeprefix("in ")
    return metric.window if words and not has_placeholder(words) and words in ctx.norm else WINDOW_PH


def _value(value: str, placeholder: str, ctx: Any) -> str:
    """A metric value stays only when it carries a number or date and every one is in the brief."""
    if has_placeholder(value) or not tokens(value) or ungrounded(value, ctx.norm):
        return placeholder
    return value

