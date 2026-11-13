"""Pure: the faithfulness and register checks on a draft, and the code fixes applied when the one
repair call did not clear them. The checks never drop a draft."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from draft import Draft
from grounding import Ground, Span, replace_spans, ungrounded
from register import MAX_SUBJECT_CHARS, register_for
from tone import NONE, Profile

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
_BULLET = re.compile(r"^(\s*)(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
_PARAGRAPHS = re.compile(r"\n\s*\n")
_CLOSER = re.compile(r"^(?i:thanks|thank you|many thanks|best|best regards|kind regards|regards|cheers|sincerely|"
                     r"warmly|talk soon|take care)[,.!]?(?:[ \t]*[-–]?[ \t]*(?:[A-Z][\w.]*|\[[^\]]{1,30}\]))?[.!]?$")
_SHORT_LINE = 30


@dataclass(frozen=True)
class Basis:
    """What a draft is checked against."""

    ground: Ground        # everything the user gave: request, context, later facts, pause answers
    addressing: Ground    # what may name the recipient: the request, pause answers, revision instructions
    profile: Profile
    length: str           # short | medium | long, for the channel's cap

    @property
    def allowed(self) -> Ground:
        """The profile's own greeting and sign-off are the user's words too."""
        return Ground.of((self.profile.greeting.replace("{first}", ""), self.profile.sign_off))


def strip_emoji(draft: Draft, profile: Profile) -> Draft:
    if profile.emoji:
        return draft
    clean = re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", draft.body)).strip()
    return replace(draft, body=clean, subject=_EMOJI.sub("", draft.subject).strip())


def problems(draft: Draft, basis: Basis) -> list[str]:
    """Every violation, worded for the repair call."""
    found = [f"'{s.text}' is not in what the user gave you: write {s.placeholder} instead"
             for s in _ungrounded(draft, basis)]
    found += _register_problems(draft, basis)
    if not _recipient_ok(draft, basis):
        found.append(f"the message is addressed to '{draft.recipient_name}', but the user never asked to write to "
                     "them: address the recipient the user named")
    return found


def _ungrounded(draft: Draft, basis: Basis) -> list[Span]:
    return [*ungrounded(draft.subject, basis.ground, basis.allowed), *ungrounded(draft.body, basis.ground, basis.allowed)]


def _register_problems(draft: Draft, basis: Basis) -> list[str]:
    reg = register_for(draft.channel)
    found = []
    if reg.subject and not draft.subject:
        found.append("an email needs a subject line")
    if reg.subject and len(draft.subject) > MAX_SUBJECT_CHARS:
        found.append(f"the subject is {len(draft.subject)} characters; keep it under {MAX_SUBJECT_CHARS}")
    if not reg.subject and draft.subject:
        found.append(f"a {reg.label.lower()} has no subject line: set subject to null")
    if not reg.sign_off and _sign_off_line(draft.body, basis.profile) is not None:
        found.append(f"a {reg.label.lower()} has no sign-off: remove the closing line")
    if not reg.bullets and _BULLET.search(draft.body):
        found.append(f"a {reg.label.lower()} has no bullet points: use sentences")
    if reg.max_paragraphs and len(_PARAGRAPHS.split(draft.body.strip())) > reg.max_paragraphs:
        found.append(f"a {reg.label.lower()} has at most {reg.max_paragraphs} paragraph(s)")
    size, cap = reg.size(draft.body), reg.cap(basis.length)
    if size > cap:
        found.append(f"the body is {size} {reg.unit}; the cap for this {reg.label.lower()} is {cap} {reg.unit}")
    return found


def _recipient_ok(draft: Draft, basis: Basis) -> bool:
    name = draft.recipient_name
    if not name:
        return True
    return basis.addressing.has(Span(0, len(name), "email" if "@" in name else "name", name))


def _sign_off_line(body: str, profile: Profile) -> int | None:
    """Index of the line where a closing sign-off block starts, or None."""
    lines = body.rstrip().split("\n")
    first = profile.sign_off.split("\n")[0].strip().casefold() if profile.sign_off != NONE else ""
    for idx in (len(lines) - 1, len(lines) - 2):
        if idx < 1:
            continue
        line = lines[idx].strip()
        tail = lines[idx + 1:]
        if (_CLOSER.match(line) or (first and line.casefold() == first)) and all(
                len(t.strip()) <= _SHORT_LINE for t in tail):
            return idx
    return None


def fix(draft: Draft, basis: Basis) -> tuple[Draft, tuple[str, ...]]:
    """Code fixes for what the repair left: placeholders for made-up specifics, register clean-up."""
    reg = register_for(draft.channel)
    body = replace_spans(draft.body, ungrounded(draft.body, basis.ground, basis.allowed))
    subject = replace_spans(draft.subject, ungrounded(draft.subject, basis.ground, basis.allowed))
    if not reg.sign_off and (idx := _sign_off_line(body, basis.profile)) is not None:
        body = "\n".join(body.rstrip().split("\n")[:idx]).rstrip()
    if not reg.bullets:
        body = _BULLET.sub(r"\1", body)
    if reg.max_paragraphs == 1:
        body = " ".join(p.strip().replace("\n", " ") for p in _PARAGRAPHS.split(body.strip()))
    subject = _fixed_subject(subject, reg.subject)
    notes: list[str] = []
    fixed = replace(draft, body=body, subject=subject)
    if not _recipient_ok(fixed, basis):
        notes.append(f"Check who this goes to: you didn't name {draft.recipient_name}.")
        fixed = replace(fixed, recipient_name="", recipient_role=fixed.recipient_role or "[recipient?]")
    size, cap = reg.size(fixed.body), reg.cap(basis.length)
    if size > cap:
        notes.append(f"(longer than usual for {reg.label.lower()}: {size} {reg.unit}, usual cap {cap}; "
                     "say 'shorter')")
    return fixed, tuple(notes)


def _fixed_subject(subject: str, required: bool) -> str:
    if not required:
        return ""
    if not subject:
        return "[subject?]"
    if len(subject) <= MAX_SUBJECT_CHARS:
        return subject
    return subject[:MAX_SUBJECT_CHARS].rsplit(" ", 1)[0].rstrip(",;:-")


def verified_facts(draft: Draft, ground: Ground) -> Draft:
    """Keep only the facts whose quoted source really is in what the user gave."""
    kept = tuple((fact, source) for fact, source in draft.facts_used
                 if re.sub(r"\s+", " ", source.casefold()).strip() in ground.text)
    return replace(draft, facts_used=kept)
