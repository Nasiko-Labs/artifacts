"""The paragraph: the model writes it with placeholders only; code checks it, fixes what it safely can,
fills in the formatted values, and falls back to a template built in code.

Checks on the model's text (the post-check is the guarantee, not the prompt):
- no digit or percent sign outside a placeholder (metric names and period labels may carry digits);
- every placeholder names a known mover, period or note;
- a cause clause ("because", "due to" ...) without a `{why:nX}` marker in its sentence is removed;
- a direction word contradicting the code's direction for that mover is replaced;
- a quoted span that is not in this turn's input or tool output drops its sentence;
- at most 120 words after filling.
A failed check (digits, unknown placeholders, length) gets one repair call, then the template."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

import prompts
from deps import TurnDeps
from facts import Facts, Note
from llm import ask_once

log = logging.getLogger("numbers_narrator.narrate")

MAX_WORDS = 120
_PLACEHOLDER = re.compile(r"\{\s*([a-z0-9_.:]+)\s*\}", re.IGNORECASE)
_DIGIT_OR_PCT = re.compile(r"[\d%]")
_SENTENCE = re.compile(r"(?:\{[^{}]*\}|[^.!?{])+[.!?]*")  # a placeholder's dot does not end a sentence
_CAUSE = re.compile(r"[,;]?\s*\b(because|due to|driven by|thanks to|as a result of|caused by|owing to|on the back of|"
                    r"fu?el+ed by)\b", re.IGNORECASE)
_QUOTE = re.compile(r"[\"“”]([^\"“”]{3,})[\"“”]")
_UP = ("rose", "rise", "rises", "risen", "increased", "increase", "increases", "grew", "grow", "grows", "climbed",
       "climb", "jumped", "gained", "up")
_DOWN = ("fell", "fall", "falls", "fallen", "decreased", "decrease", "decreases", "declined", "decline", "declines",
         "dropped", "drop", "drops", "slipped", "shrank", "down")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Checked:
    text: str = ""                # the filled paragraph when ok
    skeleton: str = ""            # the placeholder text it was filled from
    problem: str = ""             # why it must be repaired or replaced
    dropped: int = 0              # quotes or cause clauses removed

    @property
    def ok(self) -> bool:
        return not self.problem


@dataclass(frozen=True)
class Narrative:
    text: str
    skeleton: str
    used_template: bool
    first_try: bool
    dropped: int
    off_topic: bool = False


def word_count(text: str) -> int:
    return len(text.split())


def problem_of(skeleton: str, facts: Facts) -> str:
    known = set(facts.values()) | {f"why:{n.id}" for n in facts.notes}
    unknown = sorted({m.group(1) for m in _PLACEHOLDER.finditer(skeleton)} - known)
    if unknown:
        return f"unknown placeholders {unknown}"
    bare = _PLACEHOLDER.sub(" ", skeleton)
    for allowed in sorted((*facts.metric_names, facts.period_current, facts.period_previous), key=len, reverse=True):
        if allowed:
            bare = re.sub(re.escape(allowed), " ", bare, flags=re.IGNORECASE)
    if _DIGIT_OR_PCT.search(bare):
        return "a literal digit or percent sign outside the placeholders"
    if not skeleton.strip():
        return "an empty answer"
    return ""


def strip_uncited_causes(skeleton: str) -> tuple[str, int]:
    """Cause clauses survive only in a sentence that cites a note marker."""
    kept, removed = [], 0
    for sentence in _sentences(skeleton):
        cause = _CAUSE.search(sentence)
        if cause and "{why:" not in sentence.replace(" ", ""):
            head = sentence[: cause.start()].rstrip(" ,;")
            removed += 1
            if word_count(head) >= 3:
                kept.append(head + ".")
            continue
        kept.append(sentence)
    return " ".join(kept), removed


def fix_directions(skeleton: str, facts: Facts) -> str:
    by_id = {m.id: m for m in facts.movers}
    out = []
    for sentence in _sentences(skeleton):
        ids = {p.split(".")[0] for p in (m.group(1) for m in _PLACEHOLDER.finditer(sentence)) if p[:1] == "m"}
        movers = [by_id[i] for i in ids if i in by_id]
        if len(movers) == 1 and movers[0].direction in ("up", "down"):
            sentence = _replace_direction(sentence, movers[0].direction)
        out.append(sentence)
    return " ".join(out)


def _replace_direction(sentence: str, direction: str) -> str:
    wrong = _DOWN if direction == "up" else _UP

    def swap(match: re.Match) -> str:
        word = match.group(0)
        if word.casefold() in ("up", "down"):
            right = "up" if direction == "up" else "down"
        else:
            right = "rose" if direction == "up" else "fell"
        return right.capitalize() if word[:1].isupper() else right

    return re.sub(rf"\b({'|'.join(wrong)})\b", swap, sentence, flags=re.IGNORECASE)


def drop_unseen_quotes(skeleton: str, seen: str) -> tuple[str, int]:
    """A sentence quoting text that is not in this turn's input or tool output is dropped."""
    haystack = _norm(seen)
    kept, dropped = [], 0
    for sentence in _sentences(skeleton):
        quotes = [q for q in _QUOTE.findall(sentence) if "{" not in q]
        if any(_norm(q) not in haystack for q in quotes):
            dropped += 1
            continue
        kept.append(sentence)
    return " ".join(kept), dropped


def fill(skeleton: str, facts: Facts) -> str:
    values = facts.values()
    notes = {f"why:{n.id}": n for n in facts.notes}

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key in notes:
            return quote_note(notes[key])
        return values.get(key, match.group(0))

    return _WHITESPACE.sub(" ", _PLACEHOLDER.sub(sub, skeleton)).strip()


def quote_note(note: Note) -> str:
    who = "your context" if note.source == "user" else "sheet note"
    return f"\"{note.text}\" ({who})"


def cited_notes(skeleton: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(1)[4:] for m in _PLACEHOLDER.finditer(skeleton)
                               if m.group(1).startswith("why:")))


def closing(skeleton: str, facts: Facts) -> str:
    """Code-added sentences: uncited notes are still shown, and a top mover with no note says so."""
    cited = set(cited_notes(skeleton))
    extra = [f"Noted: {{why:{n.id}}}." for n in facts.notes if n.id not in cited]
    top = facts.top
    if top is not None and not facts.notes_for(top.metric):
        extra.append(no_note_line(top.metric))
    return " ".join(p for p in (skeleton.strip(), *extra) if p)


def no_note_line(metric: str) -> str:
    return f"The sheet has no notes explaining the change in {metric}."


def check(model_text: str, facts: Facts, seen: str) -> Checked:
    skeleton = model_text.strip().strip('"').strip()
    problem = problem_of(skeleton, facts)
    if problem:
        return Checked(problem=problem)
    skeleton, causes = strip_uncited_causes(skeleton)
    skeleton, quotes = drop_unseen_quotes(skeleton, seen)
    skeleton = closing(fix_directions(skeleton, facts), facts)
    text = fill(skeleton, facts)
    if word_count(text) > MAX_WORDS:
        return Checked(problem=f"{word_count(text)} words, over the {MAX_WORDS}-word limit")
    return Checked(text, skeleton, "", causes + quotes)


def template_skeleton(facts: Facts) -> str:
    parts = []
    lead = "Same week last month" if facts.compare == "month" else "Previous period"
    parts.append(f"{{period.current}} vs {{period.previous}} ({lead.lower()}):")
    for m in facts.movers:
        if m.pct == "n/a":
            parts.append(f"{{{m.id}.metric}} {m.word} by {{{m.id}.delta}} to {{{m.id}.cur}} (from zero).")
        else:
            parts.append(f"{{{m.id}.metric}} {m.word} {{{m.id}.pct}} to {{{m.id}.cur}} (from {{{m.id}.prev}}).")
    if not facts.movers:
        parts.append("No metric moved enough to call out.")
    if facts.flat:
        parts.append(f"{_join(facts.flat)} {'was' if len(facts.flat) == 1 else 'were'} flat.")
    return closing(" ".join(parts), facts)


def template(facts: Facts) -> Narrative:
    skeleton = template_skeleton(facts)
    return Narrative(fill(skeleton, facts), skeleton, True, False, 0)


async def write(deps: TurnDeps, facts: Facts, seen: str, context: str | None = None) -> Narrative:
    """One model call, one repair on a failed check, then the template."""
    if not facts.movers and not facts.flat:
        return template(facts)
    messages = [{"role": "system", "content": prompts.NARRATE_SYSTEM},
                {"role": "user", "content": prompts.fence_facts(facts.for_model())}]
    return await _attempt(deps, messages, facts, seen)


async def rewrite(deps: TurnDeps, facts: Facts, skeleton: str, instruction: str, seen: str) -> Narrative | None:
    """A follow-up rewrite under the same rules; None when both tries fail (the checked draft stands)."""
    messages = [{"role": "system", "content": prompts.REWRITE_SYSTEM},
                {"role": "user", "content": f"{prompts.fence_facts(facts.for_model())}\n\nCurrent draft:\n"
                                            f"{prompts.fence_facts(skeleton)}\n\nInstruction: {instruction}"}]
    found = await _attempt(deps, messages, facts, seen)
    return None if found.used_template and not found.off_topic else found


async def _attempt(deps: TurnDeps, messages: list[dict], facts: Facts, seen: str) -> Narrative:
    try:
        first = await ask_once(deps, messages)
    except Exception:  # the numbers still stand without a model paragraph
        log.exception("narrative call failed; using the template")
        return template(facts)
    if first.strip().upper().startswith(prompts.OFF_TOPIC):
        return replace(template(facts), off_topic=True)
    checked = check(first, facts, seen)
    if checked.ok:
        return Narrative(checked.text, checked.skeleton, False, True, checked.dropped)
    log.info("narrative rejected (%s); asking once for a repair", checked.problem)
    repair = [*messages, {"role": "assistant", "content": first},
              {"role": "user", "content": prompts.repair_instruction(checked.problem)}]
    try:
        second = await ask_once(deps, repair)
    except Exception:
        log.exception("repair call failed; using the template")
        return template(facts)
    checked = check(second, facts, seen)
    if checked.ok:
        return Narrative(checked.text, checked.skeleton, False, False, checked.dropped)
    log.info("repair rejected too (%s); using the template", checked.problem)
    return template(facts)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").replace("“", '"').replace("”", '"')).strip().casefold()


def _join(names: tuple[str, ...]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
