"""Pure: the reply, rendered in code from checked evidence (markdown only: no raw HTML, no images, links
only from tool output or pasted input). The model writes nothing about a person; every line below is
built from structured fields. Also the envelope every reply carries."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

import coverage
import keywords
from finding import Finding
from score import BREADTH_BONUS, Group, Scored

MAX_LINES = 3
WHY_LINES = 6
_UNSAFE = re.compile(r"[<>\[\]`*|\\]")
_LINK_LABEL = {"commit": "commit", "pr": "PR", "review": "PR", "answer": "thread", "doc_author": "doc",
               "doc_edit": "doc"}
_MAX_TOPIC_CHARS = 60


def clean(text: str) -> str:
    """Tool or pasted text made inert for markdown: no tags, brackets, emphasis or table pipes."""
    return " ".join(_UNSAFE.sub("", text or "").split())


def url(link: str) -> str:
    """A checked link, made safe inside markdown parentheses."""
    return link.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")


def topic_label(finding: Finding) -> str:
    words = keywords.meaningful_words(finding.gathered.topic) or finding.gathered.terms
    label = " ".join(words)
    return label if len(label) <= _MAX_TOPIC_CHARS else label[:_MAX_TOPIC_CHARS].rsplit(" ", 1)[0]


def day_text(day: date) -> str:
    return f"{day.day} {day.strftime('%b %Y')}"


def line_text(group: Group) -> str:
    n, latest = len(group.items), group.latest
    plural = "" if n == 1 else "s"
    when = day_text(latest.day)
    if group.kind == "commit":
        return f"{n} commit{plural} to `{clean(group.subject)}` (latest {when})"
    if group.kind == "pr":
        return f"opened {n} matching PR{plural} (latest {when})"
    if group.kind == "review":
        verb = "approved" if all(e.state == "APPROVED" for e in group.items) else "reviewed"
        return f"{verb} {n} matching PR{plural} (latest {when})"
    if group.kind == "answer":
        where = f" in #{clean(latest.channel)}" if latest.channel else ""
        return f'answered "{clean(group.subject)}"{where} ({when})'
    verb = "wrote" if group.kind == "doc_author" else "last edited"
    return f'{verb} "{clean(group.subject)}" ({when})'


def _links(groups: Sequence[Group]) -> str:
    links = [f"[{_LINK_LABEL[g.kind]}]({url(g.latest.link)})" for g in groups if g.latest.link]
    return " ".join(dict.fromkeys(links))


def _flags(scored: Scored) -> str:
    flags = (["weak evidence"] if scored.weak else []) + (
        ["more than one person has this name, check who is meant"] if scored.person.ambiguous else [])
    return f" ({'; '.join(flags)})" if flags else ""


def person_line(rank_no: int, scored: Scored, max_lines: int = MAX_LINES) -> str:
    groups = scored.groups[:max_lines]
    body = "; ".join(line_text(g) for g in groups)
    links = _links(groups)
    tail = f"\n   {links}" if links else ""
    return f"{rank_no}. **{clean(scored.person.name)}**{_flags(scored)} - {body}{tail}"


def headline(finding: Finding) -> str:
    topic = topic_label(finding)
    if not finding.shown:
        return f'**No clear owner for "{topic}"**: I found no evidence of anyone working on it in what I read.'
    if not finding.clear_owner:
        return f'**No clear owner for "{topic}"**: the evidence I found is thin.'
    names = [clean(s.person.name) for s in finding.shown]
    return f'**Most likely to know "{topic}": {", then ".join(names)}**'


def coverage_lines(finding: Finding) -> tuple[str, ...]:
    gathered = finding.gathered
    if gathered.mode == "pasted":
        lines = ["Coverage: pasted evidence only, not checked against any source."]
    else:
        read = [coverage.LABELS[s] for s in gathered.read_sources()]
        lines = [f"Coverage: {', '.join(read)} read." if read else "Coverage: no source could be read."]
        missing = [f"{coverage.LABELS[s]} ({gathered.statuses[s][1] or gathered.statuses[s][0]})"
                   for s in coverage.SOURCES if s in gathered.statuses and gathered.statuses[s][0] != "ok"]
        if missing:
            lines.append(f"Not read: {'; '.join(missing)}.")
        if "code" in gathered.read_sources():
            lines.append("Note: commit authorship stands in for blame.")
    if gathered.dropped:
        lines.append(f"Left out {gathered.dropped} item(s) I could not find word for word in the tool output.")
    if gathered.salvaged:
        lines.append("Some results were rebuilt after a step limit, so they may be incomplete.")
    if finding.me_unknown:
        lines.append("I couldn't tell who you are from your connected tools, so you may appear above.")
    if gathered.repo and gathered.repo != "*":
        lines.append(f"Code evidence only from {clean(gathered.repo)}.")
    return tuple(lines)


def render(finding: Finding) -> str:
    people = [person_line(i, s) for i, s in enumerate(finding.shown, start=1)]
    more = "Say \"more\" for the next two, or \"why\" and a name for that person's evidence." \
        if len(finding.ranked) > 2 else ("Say \"why\" and a name for that person's evidence." if finding.shown else "")
    blocks = [headline(finding), "\n".join(people), "\n".join(coverage_lines(finding)), more]
    return "\n\n".join(b for b in blocks if b)


def render_more(finding: Finding) -> str:
    rest = finding.ranked[2:4]
    if not rest:
        return f'Nobody else has evidence on "{topic_label(finding)}" in what I read.'
    lines = [person_line(i, s) for i, s in enumerate(rest, start=3)]
    return "\n\n".join([f'**Next with evidence on "{topic_label(finding)}"**', "\n".join(lines)])


def find_person(finding: Finding, who: str) -> tuple[int, Scored] | None:
    """"#2" by rank, else a name (or first or last name) of someone ranked."""
    wanted = who.strip().casefold()
    if wanted.startswith("#") and wanted[1:].isdigit():
        index = int(wanted[1:]) - 1
        return (index + 1, finding.ranked[index]) if 0 <= index < len(finding.ranked) else None
    for i, scored in enumerate(finding.ranked, start=1):
        name = scored.person.name.casefold()
        if wanted == name or wanted in name.split() or (len(wanted) > 2 and name.startswith(wanted)):
            return i, scored
    return None


def render_why(finding: Finding, who: str) -> str:
    found = find_person(finding, who)
    if found is None:
        names = ", ".join(clean(s.person.name) for s in finding.ranked[:4]) or "nobody"
        return f'I have no evidence about "{clean(who)}" for this topic. People with evidence: {names}.'
    rank_no, scored = found
    lines = [f"- {line_text(g)}" + (f" [{_LINK_LABEL[g.kind]}]({url(g.latest.link)})" if g.latest.link else "")
             for g in scored.groups[:WHY_LINES]]
    parts = [f"{kind.replace('_', ' ')} {value:.2f}" for kind, value in sorted(scored.by_kind.items(),
                                                                              key=lambda kv: -kv[1])]
    if scored.breadth >= 2:
        parts.append(f"breadth bonus {BREADTH_BONUS:.2f}")
    sources = ", ".join(coverage.LABELS[s] for s in coverage.SOURCES if s in scored.person.sources)
    head = f"**Why {clean(scored.person.name)}** (#{rank_no}{_flags(scored)})"
    score = (f"Score {scored.score:.2f} = {' + '.join(parts)} (computed in code: weight x recency, half-life 180 "
             f"days). Evidence from: {sources}.")
    return "\n\n".join([head, "\n".join(lines), score])


def person_dict(rank_no: int, scored: Scored) -> dict:
    person = scored.person
    handles = {"code": _first(person.handles.get("code")), "chat": _first(person.handles.get("chat")),
               "email": person.emails[0] if person.emails else None}
    evidence = [{"kind": g.kind, "detail": line_text(g), "date": g.latest.day.isoformat(),
                 "link": g.latest.link or None, "source": g.latest.source} for g in scored.groups[:MAX_LINES]]
    return {"name": person.name, "handles": handles, "score_rank": rank_no, "score": scored.score,
            "evidence": evidence, "breadth": scored.breadth, "weak": scored.weak, "ambiguous": person.ambiguous}


def envelope(finding: Finding) -> dict:
    people = [person_dict(i, s) for i, s in enumerate(finding.shown, start=1)]
    return {"summary": headline(finding).replace("**", ""), "items": people, "confidence": finding.confidence,
            "needs_human": finding.needs_human, "people": people, "topic": topic_label(finding),
            "terms": list(finding.gathered.terms), "coverage": list(coverage_lines(finding))}


def _first(values: Sequence[str] | None) -> str | None:
    return values[0] if values else None
