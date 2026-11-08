"""Pure: per-person examples from records + attributions + written examples. Identity rules in identity.py;
a PR and a feature naming the same PR number or link become one example (kind shipped, the PR as evidence);
a co-owned feature appears under every owner; vague praise is counted, never an example; records nobody is
credited in are listed with the reason. People are alphabetical; examples newest first. No ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import grounding
import identity
from attribute import Attribution
from records import PR, PRAISE, SHIPPED, Record
from write import FALLBACK, Written

COUNT_KEYS = {PR: "prs", PRAISE: "praise", SHIPPED: "shipped"}


def merges(records: Sequence[Record]) -> dict[str, str]:
    """PR id -> the feature id that names it."""
    prs = [r for r in records if r.kind == PR]
    out = {}
    for f in (r for r in records if r.kind == SHIPPED and r.refs):
        for p in prs:
            if p.keys & set(f.refs) and p.id not in out:
                out[p.id] = f.id
    return out


def _evidence(record: Record, merged_pr: Record | None) -> str:
    source = merged_pr or (record if record.kind == PR else None)
    if source is not None and (source.number or source.url):
        bits = [f"PR #{source.number}" if source.number else "", source.url]
        return ", ".join(b for b in bits if b)
    if record.kind == PRAISE:
        return f'"{grounding.quote(record)}"'
    return record.url or "from the shipped list"


def example_text(record: Record, written: Written) -> str:
    if record.kind != PRAISE:
        return written.text
    by = f"Praised by {record.speaker}" if record.speaker else "Praised"
    if written.source == FALLBACK:
        return f"{by}: {written.text}"
    phrase = written.text.rstrip(".")
    if phrase[:1].isupper() and phrase[1:2].islower():
        phrase = phrase[:1].lower() + phrase[1:]
    return f"{by} for {phrase}."


def _person(people: dict, ident: identity.Identity, raw: str) -> dict:
    person = people.setdefault(ident.key, {"name": ident.name, "kind": ident.kind, "aliases": [], "examples": [],
                                           "counts": {"prs": 0, "praise": 0, "shipped": 0, "unspecific_praise": 0}})
    if raw.startswith("@") or ident.kind == identity.HANDLE:
        handle = "@" + raw.lstrip("@")
        if handle not in person["aliases"]:
            person["aliases"].append(handle)
    return person


def _credited(record: Record, names: Sequence[str], aliases: dict[str, str]) -> list[tuple[identity.Identity, str]]:
    out, seen = [], set()
    for name in names:
        ident = identity.resolve(name, aliases, is_login=record.kind == PR and name == record.author)
        if ident and ident.key not in seen:
            seen.add(ident.key)
            out.append((ident, name))
    return out


def group(records: Sequence[Record], attributions: Mapping[str, Attribution], written: Mapping[str, Written],
          specific: Mapping[str, bool], aliases: dict[str, str]) -> dict:
    by_id = {r.id: r for r in records}
    merged = merges(records)
    merged_into = {f: p for p, f in merged.items()}
    people: dict[str, dict] = {}
    unattributed, vague = [], []
    for r in records:
        if r.id in merged:
            continue
        names = list(attributions.get(r.id, Attribution(())).people)
        pr = by_id.get(merged_into.get(r.id, ""))
        if pr is not None:
            names += list(attributions.get(pr.id, Attribution(())).people)
        credited = _credited(r, names, aliases)
        if r.kind == PRAISE and not specific.get(r.id, False):
            vague.append({"record_id": r.id, "speaker": r.speaker, "quote": grounding.quote(r),
                          "people": [i.name for i, _ in credited]})
            for ident, raw in credited:
                _person(people, ident, raw)["counts"]["unspecific_praise"] += 1
            continue
        if not credited:
            reason = "no author" if r.kind == PR else "no name found"
            unattributed.append({"record_id": r.id, "kind": r.kind, "text": grounding.quote(r) if r.kind == PRAISE
                                 else (r.title or r.text)[:200], "reason": reason})
            continue
        w = written.get(r.id) or Written(grounding.fallback(r), FALLBACK)
        example = {"kind": r.kind, "text": example_text(r, w), "evidence": _evidence(r, pr), "date": r.date,
                   "record_id": r.id, "source": w.source}
        for ident, raw in credited:
            person = _person(people, ident, raw)
            person["examples"].append(example)
            person["counts"][COUNT_KEYS[r.kind]] += 1
    persons = sorted(people.values(), key=sort_key)
    for p in persons:
        p["examples"] = sorted(p["examples"], key=lambda e: (e["date"] is None, _neg(e["date"])))
    return {"per_person": persons, "unattributed": unattributed, "vague": vague}


def sort_key(person: Mapping) -> tuple:
    order = {identity.ALIAS: 0, identity.FULL: 0, identity.HANDLE: 1, identity.FIRST: 2}
    return order.get(person["kind"], 1), person["name"].lstrip("@").casefold()


def _neg(iso: str | None) -> str:
    """Sort key for newest first: invert each digit of the ISO date."""
    return "".join(chr(ord("9") - ord(c) + ord("0")) if c.isdigit() else c for c in (iso or ""))
