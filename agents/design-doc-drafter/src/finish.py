"""Pure: from the stored draft to the reply. Grounding (numbers -> [TBD], unnamed technologies -> generic),
open questions (stable numbers), the assembled document, the envelope and the rendered reply. Run after
every turn that changes the draft, so coverage and questions are always recomputed from the current state.

The stored design and sections stay ungrounded: when a later answer supplies a number, text that used it
becomes valid again."""

from __future__ import annotations

from dataclasses import dataclass

import assemble
import checks
import coverage
import gaps
import render


@dataclass(frozen=True)
class Finished:
    doc: dict           # the stored draft with updated question numbers
    envelope: dict
    replies: tuple[str, ...]


def ground_text(doc: dict) -> str:
    reqs = "\n".join(r["text"] for r in doc["reqs"])
    return "\n".join([doc["source"], reqs, *doc.get("context", ()), doc.get("guidance", ""),
                      *doc.get("answers", ())])


def finish(doc: dict, wants_json: bool, extra_notes: tuple[str, ...] = ()) -> Finished:
    ground = ground_text(doc)
    grounded = checks.ground(doc["design"], doc["sections"], ground)
    design, dropped_steps = checks.drop_unknown(grounded.design)
    sections, reqs = grounded.sections, doc["reqs"]
    unverified = [] if doc.get("adr") else checks.unverified(sections, reqs)
    found = gaps.candidates(reqs, ground, checks.uncovered(design, reqs), unverified, grounded.tbd_units)
    questions, qnums = gaps.number(found, doc.get("qnums") or {}, doc.get("answered") or ())
    decisions = [{**d, "given": checks.decision_given(d["choice"], ground)} for d in design.get("decisions") or ()]
    title = doc.get("title") or "draft"
    if doc.get("adr"):
        parts = assemble.adr(title, sections, decisions, questions)
    else:
        parts = assemble.document(title, reqs, design, sections, decisions, questions, bool(doc.get("agent")),
                                  doc.get("context") or ())
    markdown = assemble.join(parts)
    dropped = {"requirements": doc.get("dropped_requirements", 0), "steps": dropped_steps}
    env = coverage.envelope(title, reqs, design, sections, questions, markdown, bool(doc.get("repaired")), dropped)
    notes = render.notes(env, doc.get("skipped_chars", 0), grounded.generic, extra_notes)
    replies = render.render_json(env) if wants_json else render.render(parts, env, notes)
    return Finished({**doc, "qnums": qnums, "questions": questions}, env, tuple(replies))
