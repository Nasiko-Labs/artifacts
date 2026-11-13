"""How-to answers: the model writes from the top retrieved sections only; code renders the reply. Without
a model (no traceparent, a failed call, or an unusable answer) the best section is rendered as written."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import prompts
import render
from kb import Section
from llm import Budget, BudgetExhausted, MissingTraceparent, TurnDeps, ask_json
from retrieve import Hit

log = logging.getLogger("support.answer")

MAX_CLI = 5
FLOW_PREFERENCE = 0.8   # without a model, a flow within this share of the top docs chunk's score answers


def _strings(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()][:limit]


def from_section(section: Section, snapshot_date: str, lead: str = "", setup: Section | None = None) -> render.Reply:
    """The section itself, no model: a flow's own steps, or a docs excerpt. A docs excerpt answering a
    developer-integration question also gets the `setup` flow's menu path and commands."""
    if section.kind == "flow":
        return render.howto(section, section.steps, section.cli[:MAX_CLI], section.good_to_know,
                            snapshot_date=snapshot_date, lead=lead)
    excerpt = "\n\n".join(p for p in (lead, "From the docs:\n" + render.doc_excerpt(section)) if p)
    if setup is not None:
        return render.howto(section, [], setup.cli[:MAX_CLI], f"to set it up, see {setup.title}.",
                            where=setup.where, snapshot_date=snapshot_date, lead=excerpt)
    return render.howto(section, [], section.cli[:MAX_CLI], "", snapshot_date=snapshot_date, lead=excerpt)


def fallback_section(hits: Sequence[Hit], prefer_docs: bool = False) -> Section:
    """The section to render without a model: the top hit, unless a flow scores close to it (a flow's own
    steps read better than a raw docs excerpt). A developer-integration question keeps the top docs hit."""
    top = hits[0]
    if top.section.kind == "flow" or prefer_docs:
        return top.section
    flow = next((h for h in hits if h.section.kind == "flow"), None)
    return flow.section if flow is not None and flow.score >= FLOW_PREFERENCE * top.score else top.section


def _fallback(hits: Sequence[Hit], snapshot_date: str, lead: str, prefer_docs: bool) -> render.Reply:
    return from_section(fallback_section(hits, prefer_docs), snapshot_date, lead, _setup_flow(hits, prefer_docs))


def _setup_flow(hits: Sequence[Hit], prefer_docs: bool) -> Section | None:
    """For a developer-integration question, the flow among the hits: its menu and commands set it up."""
    return next((h.section for h in hits if h.section.kind == "flow"), None) if prefer_docs else None


def _from_model(data: dict, hits: Sequence[Hit], snapshot_date: str, lead: str,
                setup: Section | None = None) -> render.Reply | None:
    if data.get("found") is False:
        return render.not_in_notes(snapshot_date)
    by_id = {h.section.id: h.section for h in hits}
    section = by_id.get(str(data.get("section_id") or "")) or hits[0].section
    steps = _strings(data.get("steps"), render.MAX_STEPS)
    if not steps:
        return None
    setup_cli = list(setup.cli[:MAX_CLI]) if setup is not None else []
    cli = _strings(data.get("cli"), MAX_CLI) or list(section.cli[:MAX_CLI]) or setup_cli
    where = data.get("where") if isinstance(data.get("where"), str) else ""
    if not where.strip() and setup is not None and section.kind != "flow":
        where = setup.where
    good = data.get("good_to_know") if isinstance(data.get("good_to_know"), str) else ""
    return render.howto(section, steps, cli, good, where=(where or "").strip(" *"), snapshot_date=snapshot_date,
                        lead=lead)


async def compose(question: str, hits: Sequence[Hit], deps: TurnDeps, snapshot_date: str,
                  lead: str = "", prefer_docs: bool = False) -> render.Reply:
    """`hits` is non-empty: the caller has already decided the notes cover the question. `prefer_docs`: a
    developer-integration question, answered from the docs first."""
    if not deps.traceparent:
        return _fallback(hits, snapshot_date, lead, prefer_docs)
    try:
        data = await ask_json(deps, Budget(), prompts.messages(question, [h.section for h in hits], prefer_docs))
    except (MissingTraceparent, BudgetExhausted, TimeoutError) as e:
        log.warning("model call skipped or timed out: %s", type(e).__name__)
        return _fallback(hits, snapshot_date, lead, prefer_docs)
    except Exception:
        log.exception("model call failed; answering from the notes as written")
        return _fallback(hits, snapshot_date, lead, prefer_docs)
    reply = _from_model(data, hits, snapshot_date, lead, _setup_flow(hits, prefer_docs)) if data else None
    return reply or _fallback(hits, snapshot_date, lead, prefer_docs)
