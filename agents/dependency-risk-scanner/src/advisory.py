"""The advisory the user supplies, read in code first: ids, package, ecosystem, affected ranges, fixed
versions, a verbatim summary, or a deprecation notice. A model gap-fill is accepted field by field
only when the field appears verbatim in the paste (`fill_from_model`). Pure: no I/O."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace

import ranges
import versions
from advisory_text import (
    ecosystem_in,
    fixed_in,
    ids_in,
    package_in,
    ranges_in,
    replacement_in,
    summary_in,
    verbatim,
)

REQUIRED = ("package", "ecosystem", "affected")
_SIGNAL = re.compile(r"\b(?:advisory|vulnerab\w*|deprecat\w*|security|exploit\w*|affected|affects|fixed in|"
                     r"patched|cve|ghsa)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Advisory:
    id: str = ""
    ecosystem: str = ""
    package: str = ""
    affected: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()
    summary: str = ""
    kind: str = "vulnerability"   # "vulnerability" | "deprecation"
    replacement: str = ""
    range_waived: bool = False    # the user chose to scan without a range: statuses read `unknown`

    def intervals(self) -> ranges.Intervals | None:
        if not self.affected or not self.ecosystem:
            return None
        parts = [ranges.parse_affected(self.ecosystem, a) for a in self.affected]
        return None if any(p is None for p in parts) else tuple(i for p in parts if p for i in p)

    def missing(self) -> tuple[str, ...]:
        gaps = []
        if not self.package:
            gaps.append("package")
        if not self.ecosystem:
            gaps.append("ecosystem")
        if self.kind == "vulnerability" and not self.affected and not self.range_waived:
            gaps.append("affected")
        return tuple(gaps)

    def to_dict(self) -> dict:
        return {**asdict(self), "affected": list(self.affected), "fixed": list(self.fixed)}


@dataclass(frozen=True)
class Parsed:
    advisory: Advisory
    signal: bool = False   # the text reads as an advisory at all

    @property
    def missing(self) -> tuple[str, ...]:
        return self.advisory.missing()


def parse_text(text: str) -> Parsed:
    structured = _json_advisory(text)
    if structured is not None:
        return Parsed(structured, True)
    found_ids = ids_in(text)
    eco = ecosystem_in(text)
    affected = ranges_in(text)
    fixed = fixed_in(text) or _derived_fixed(affected)
    deprecated = bool(re.search(r"\bdeprecat", text, re.IGNORECASE)) and not found_ids and not affected
    adv = Advisory(id=found_ids[0] if found_ids else "", ecosystem=eco, package=package_in(text), affected=affected,
                   fixed=fixed, summary=summary_in(text, found_ids),
                   kind="deprecation" if deprecated else "vulnerability",
                   replacement=replacement_in(text) if deprecated else "")
    signal = bool(found_ids or affected or _SIGNAL.search(text or ""))
    return Parsed(adv, signal)


def _derived_fixed(affected: tuple[str, ...]) -> tuple[str, ...]:
    """`<4.17.21` alone names its fix: the exclusive upper bound."""
    found = []
    for item in affected:
        uppers = re.findall(r"<\s*(v?\d[\w.\-+]*)", item)
        if len(uppers) == 1 and "<=" not in item:
            found.append(uppers[0])
    return tuple(dict.fromkeys(found))


def from_mapping(data: Mapping) -> Advisory | None:
    """The small JSON form (also an envelope's `advisory`). None without a package."""
    package = str(data.get("package") or "").strip()
    if not package:
        return None
    eco = versions.ecosystem(str(data.get("ecosystem") or "")) or ""
    affected = data.get("affected") or ()
    fixed = data.get("fixed") or ()
    return Advisory(id=str(data.get("id") or ""), ecosystem=eco, package=package,
                    affected=tuple(str(a) for a in (affected if isinstance(affected, list) else [affected]) if a),
                    fixed=tuple(str(f) for f in (fixed if isinstance(fixed, list) else [fixed]) if f),
                    summary=str(data.get("summary") or "")[:300],
                    kind="deprecation" if data.get("kind") == "deprecation" else "vulnerability",
                    replacement=str(data.get("replacement") or ""))


def _json_advisory(text: str) -> Advisory | None:
    try:
        data = json.loads((text or "").strip())
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    if isinstance(data.get("affected"), list) and data["affected"] and isinstance(data["affected"][0], Mapping):
        return _osv(data)
    return from_mapping(data) if "package" in data else None


def _osv(data: Mapping) -> Advisory | None:
    first = data["affected"][0]
    package = first.get("package") if isinstance(first.get("package"), Mapping) else {}
    eco = versions.ecosystem(str(package.get("ecosystem") or "")) or ""
    affected, fixed = [], []
    for rng in first.get("ranges") or ():
        events = [e for e in (rng.get("events") or ()) if isinstance(e, Mapping)]
        intro = next((str(e["introduced"]) for e in events if "introduced" in e), "0")
        ends = [(k, str(e[k])) for e in events for k in ("fixed", "last_affected") if k in e]
        for kind, end in ends or [("", "")]:
            parts = ([f">={intro}"] if intro != "0" else []) + ([f"<{end}"] if kind == "fixed" else
                                                                [f"<={end}"] if kind else [])
            affected.append(",".join(parts) or "*")
            if kind == "fixed":
                fixed.append(end)
    if not package.get("name"):
        return None
    return Advisory(id=str(data.get("id") or ""), ecosystem=eco, package=str(package["name"]), affected=tuple(affected),
                    fixed=tuple(dict.fromkeys(fixed)), summary=str(data.get("summary") or "")[:300])


def fill_from_model(parsed: Parsed, model: Mapping, paste: str) -> Parsed:
    """Fill only the fields code left empty, and only with values found verbatim in the paste."""
    adv = parsed.advisory
    updates: dict = {}
    if not adv.id and verbatim(str(model.get("id") or ""), paste):
        updates["id"] = str(model["id"]).strip()
    if not adv.package and verbatim(str(model.get("package") or ""), paste):
        updates["package"] = str(model["package"]).strip().strip("`'\"")
    eco_word = str(model.get("ecosystem") or "")
    if not adv.ecosystem and verbatim(eco_word, paste) and versions.ecosystem(eco_word):
        updates["ecosystem"] = versions.ecosystem(eco_word)
    if not adv.affected:
        kept = tuple(a for a in _strings(model.get("affected")) if _versions_seen(a, paste))
        updates["affected"] = kept
    if not adv.fixed:
        kept = tuple(f for f in _strings(model.get("fixed")) if verbatim(f, paste))
        updates["fixed"] = kept or _derived_fixed(updates.get("affected", ()))
    if not adv.summary and verbatim(str(model.get("summary") or ""), paste):
        updates["summary"] = str(model["summary"]).strip()[:300]
    return replace(parsed, advisory=replace(adv, **updates))


def answer_range(pending: Advisory, answer: str) -> Parsed:
    """The reply to "What is the affected range?": ecosystem words and ranges in it fill the gaps;
    "scan without a range" waives the range."""
    text = answer or ""
    if re.search(r"without a range|don'?t know|no range|any version|unknown", text, re.IGNORECASE):
        waived = replace(pending, range_waived=True, ecosystem=pending.ecosystem or ecosystem_in(text))
        return Parsed(waived, True)
    eco = pending.ecosystem or ecosystem_in(text)
    affected = pending.affected or ranges_in(text) or _bare_ranges(text)
    filled = replace(pending, ecosystem=eco, affected=affected, package=pending.package or package_in(text),
                     fixed=pending.fixed or fixed_in(text) or _derived_fixed(affected))
    return Parsed(filled, bool(affected))


def _bare_ranges(text: str) -> tuple[str, ...]:
    stripped = re.sub(r"\b(?:npm|pip|pypi|go|golang|cargo|maven|gradle|gem|rubygems)\b[,:]?", "", text,
                      flags=re.IGNORECASE).strip(" ,.")
    return (stripped,) if stripped and ranges.parse_affected("npm", stripped) is not None else ()


def _strings(value: object) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    return [str(i).strip() for i in items if str(i).strip()]


def _versions_seen(range_text: str, paste: str) -> bool:
    found = re.findall(r"\d+(?:\.\d+)+[\w\-+]*|\d+", range_text)
    return bool(found) and all(verbatim(v, paste) for v in found)
