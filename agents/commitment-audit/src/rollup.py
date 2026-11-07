"""Pure: the data the rollup model words, and the code check that makes its markdown true.

The model never states a status: each row carries a token `{{status:<cid>}}` that code replaces with
the derived statuses, so a rewritten or invented token cannot change one. After substitution, code
removes links no row carries, rewrites any line that names one commitment with a status phrase that
row does not have, and appends a bullet for every commitment (or disagreement) the text left out."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from derive import LABELS, Row
from render import disagreement_text
from verbatim import normalise_text

DISAGREEMENTS = "Disagreements"
AT_RISK = "At risk"
ON_TRACK = "On track"
NOT_AUDITED = "Not audited"
SUMMARY = "Summary"
SECTIONS = (SUMMARY, DISAGREEMENTS, AT_RISK, ON_TRACK, NOT_AUDITED)
LINK_REMOVED = "[link removed]"

_TOKEN = re.compile(r"\{\{\s*status\s*:\s*([A-Za-z0-9_-]+)[^{}]*\}\}", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)\]>\"'`]+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_TRACK_PHRASES = {"on_track": re.compile(r"\bon[ -]track\b", re.IGNORECASE), "at_risk": re.compile(r"\bat[ -]risk\b", re.IGNORECASE),
                  "off_track": re.compile(r"\boff[ -]track\b", re.IGNORECASE)}
_CODE_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n?|\n?```\s*$")


def token(cid: str) -> str:
    return "{{status:" + cid + "}}"


def status_text(row: Row) -> str:
    return (f"reported {LABELS.get(row.reported, row.reported)}, evidence {LABELS.get(row.evidence, row.evidence)}"
            f" ({row.confidence.lower()} confidence)")


def section_of(row: Row) -> str:
    if row.disagreement:
        return DISAGREEMENTS
    return ON_TRACK if row.evidence in ("on_track", "done") else AT_RISK


def model_data(rows: Sequence[Row], not_audited: Sequence[str]) -> str:
    """The rows as the rollup model sees them (fenced by the caller): no status words, only tokens."""
    items = [{"cid": r.cid, "name": r.name, "owner": r.owner, "section": section_of(r), "status": token(r.cid),
              "disagreement": disagreement_text(r) or None, "risks": list(r.risks), "links": list(r.links)}
             for r in rows]
    return json.dumps({"commitments": items, "not_audited": list(not_audited)}, ensure_ascii=False, indent=1)


@dataclass
class _Section:
    heading: str | None
    lines: list[str]

    @property
    def key(self) -> str | None:
        name = (self.heading or "").casefold()
        return next((s for s in SECTIONS if s.casefold() in name), None)


def finalize(text: str, rows: Sequence[Row], not_audited: Sequence[str], title: str) -> str:
    by_cid = {r.cid: r for r in rows}
    body = _CODE_FENCE.sub("", (text or "").strip())
    tokened = {m.group(1) for m in _TOKEN.finditer(body) if m.group(1) in by_cid}
    body = _TOKEN.sub(lambda m: status_text(by_cid[m.group(1)]) if m.group(1) in by_cid else "", body)
    body = _strip_unknown_links(body, rows)
    lines = [_true_line(line, rows) for line in body.splitlines()]
    sections = _sections(lines, title)
    for r in rows:
        if r.cid not in tokened or not _names(sections, r.name):
            _append(sections, section_of(r), _bullet(r))
        elif r.disagreement and not _names([_find(sections, DISAGREEMENTS)], r.name):
            _append(sections, DISAGREEMENTS, _bullet(r))
    for name in not_audited:
        if not _names(sections, name):
            _append(sections, NOT_AUDITED, f"- {name}: not audited this run.")
    if _find(sections, SUMMARY) is None or not "".join(_find(sections, SUMMARY).lines).strip():
        _append(sections, SUMMARY, _summary(rows, not_audited))
    return _join(sections)


def _strip_unknown_links(body: str, rows: Sequence[Row]) -> str:
    allowed = {link for r in rows for link in (*r.links, *_URL.findall(" ".join(r.risks)))}
    return _URL.sub(lambda m: m.group(0) if m.group(0).rstrip(".,;:") in allowed or m.group(0) in allowed
                    else LINK_REMOVED, body)


def _true_line(line: str, rows: Sequence[Row]) -> str:
    """A line naming exactly one commitment may only use that row's own status phrases."""
    named = [r for r in rows if normalise_text(r.name) in normalise_text(line)]
    if len(named) != 1 or _HEADING.match(line):
        return line
    r = named[0]
    wrong = [s for s, phrase in _TRACK_PHRASES.items() if phrase.search(line) and s not in (r.reported, r.evidence)]
    return _bullet(r) if wrong else line


def _sections(lines: list[str], title: str) -> list[_Section]:
    sections = [_Section(None, [])]
    for line in lines:
        match = _HEADING.match(line)
        if match:
            sections.append(_Section(line, []))
        else:
            sections[-1].lines.append(line)
    if not any(s.heading and s.heading.lstrip().startswith("# ") for s in sections):
        sections[0].lines.insert(0, f"# {title}")
    return sections


def _find(sections: list[_Section], key: str) -> _Section | None:
    return next((s for s in sections if s.key == key), None)


def _names(sections: Sequence[_Section | None], name: str) -> bool:
    wanted = normalise_text(name)
    return any(s is not None and wanted in normalise_text(" ".join([s.heading or "", *s.lines])) for s in sections)


def _append(sections: list[_Section], key: str, line: str) -> None:
    section = _find(sections, key)
    if section is None:
        section = _Section(f"## {key}", [])
        sections.insert(_insert_at(sections, key), section)
    while section.lines and not section.lines[-1].strip():
        section.lines.pop()
    section.lines.append(line)


def _insert_at(sections: list[_Section], key: str) -> int:
    """Before the first section that comes later in SECTIONS order; else at the end."""
    order = SECTIONS.index(key)
    later = [i for i, s in enumerate(sections) if s.key is not None and SECTIONS.index(s.key) > order]
    return max(1, later[0] if later else len(sections))


def _bullet(row: Row) -> str:
    flagged = f" Disagreement: {disagreement_text(row)}." if row.disagreement else ""
    risk = f" Risk: {'; '.join(row.risks)}." if row.risks else ""
    return f"- **{row.name}**: {status_text(row)}.{flagged}{risk}"


def _summary(rows: Sequence[Row], not_audited: Sequence[str]) -> str:
    flagged = sum(1 for r in rows if r.disagreement)
    risky = sum(1 for r in rows if r.evidence in ("at_risk", "off_track"))
    extra = f" {len(not_audited)} not audited this run." if not_audited else ""
    return (f"{len(rows)} commitments audited: {flagged} where the reported status disagrees with the evidence, "
            f"{risky} at risk or off track by the evidence.{extra}")


def _join(sections: list[_Section]) -> str:
    blocks = []
    for s in sections:
        body = "\n".join(s.lines).strip("\n")
        block = "\n".join(x for x in (s.heading, body) if x)
        if block.strip():
            blocks.append(block)
    return "\n\n".join(blocks).strip()
