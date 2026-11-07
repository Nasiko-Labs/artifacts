"""The analyse half of a pull: extract -> cluster -> themes (code) -> customers (CRM) -> themes again with ARR ->
quotes -> churn -> link (tracker) -> the envelope. Every step degrades on its own: a failed or cut model call
leaves a code fallback and a caveat, never a guess."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import churn
import quotes
from caveats import RunFacts
from cluster import cluster_step
from deps import TurnDeps
from extract import extract_step
from linking import link_step
from parse_paste import Request
from reader import TurnReader
from records import Item
from report import Found, envelope
from steps_crm import customers_step, names_to_ask, with_arr
from themes import compute
from window import Window


def labels_by_theme(items: Sequence[Item], theme_of: Mapping[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in items:
        if item.id in theme_of and item.label and item.label not in out.get(theme_of[item.id], []):
            out[theme_of[item.id]] = [*out.get(theme_of[item.id], []), item.label]
    return out


async def analyse(deps: TurnDeps, turn: TurnReader, request: Request, window: Window, items: tuple[Item, ...],
                  facts: RunFacts) -> dict:
    extracted = await extract_step(deps, items)
    clustered = await cluster_step(deps, extracted.items)
    theme_of = {i.id: clustered.theme_of_label[i.label] for i in extracted.items
                if i.label in clustered.theme_of_label}
    labels_of = labels_by_theme(extracted.items, theme_of)
    stats, _ = compute(extracted.items, clustered.themes, theme_of)
    arrs = await customers_step(deps, turn, names_to_ask(extracted.items, stats, deps.config.max_crm_names))
    priced = with_arr(extracted.items, arrs.by_key)
    stats, data = compute(priced, clustered.themes, theme_of)
    by_id = {i.id: i for i in priced}
    quote_ids, quotes_cut = await quotes.pick(deps, stats, by_id)
    checked = await churn.confirm(deps, priced)
    issues, link_run = await link_step(deps, turn, stats, labels_of, request.team)
    facts = replace(
        facts, notes=(*facts.notes, *arrs.run.notes, *link_run.notes), unreached=extracted.unreached,
        churn_unchecked=checked.unchecked, customers_without_arr=data.customers - data.customers_with_arr,
        crm_read=arrs.run.read, tracker_read=link_run.read,
        cut=extracted.cut or clustered.cut or quotes_cut or checked.cut,
        salvage=facts.salvage or arrs.run.limited or link_run.limited)
    found = Found(window, priced, tuple(stats), data, labels_of, quote_ids, checked.flagged, issues, facts)
    return envelope(found)
