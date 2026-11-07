"""Pure: parse -> rank -> census -> probes for one diff, with the coverage notes (what was analysed, what was
censused only, what was skipped and why). No model call happens here."""

from __future__ import annotations

from dataclasses import dataclass, field

import census
import diff_parse
import probes

MAX_ANALYSED_CHARS = 100_000
CENSUS_ONLY_KINDS = ("lock", "generated")
HEAVY_KINDS = ("migration", "config", "ci", "api-schema", "prompt")
LIGHT_KINDS = ("docs", "test", "generated")


@dataclass
class Analysis:
    files: list[diff_parse.DiffFile]
    fired: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)


def risk_score(f: diff_parse.DiffFile) -> int:
    removed_symbol = f.kind == "source" and any(
        name not in census.symbols_in(f.changed("+")) for name in census.symbols_in(f.changed("-")))
    return (3 * (f.kind in HEAVY_KINDS) + 2 * (f.status in ("deleted", "renamed")) + 1 * removed_symbol
            - 2 * (f.kind in LIGHT_KINDS))


def rank(files: list[diff_parse.DiffFile], limit: int = MAX_ANALYSED_CHARS
         ) -> tuple[list[diff_parse.DiffFile], list[diff_parse.DiffFile]]:
    """(analysed, skipped): highest risk first, until the size cap. Order within the diff breaks ties."""
    if sum(f.chars for f in files) <= limit:
        return files, []
    ordered = sorted(enumerate(files), key=lambda p: (-risk_score(p[1]), p[0]))
    kept, skipped, used = [], [], 0
    for _, f in ordered:
        if used + f.chars <= limit or not kept:
            kept.append(f)
            used += f.chars
        else:
            skipped.append(f)
    return kept, skipped


def analyse(diff_text: str, context: str = "", limit: int = MAX_ANALYSED_CHARS) -> Analysis:
    files = diff_parse.parse(diff_text)
    kept, skipped = rank(files, limit)
    facts = census.build(kept).facts
    fired = probes.fire(facts, kept, diff_text, context)
    coverage = {
        "files": len(files),
        "analysed": [f.path for f in kept if f.kind not in CENSUS_ONLY_KINDS and f.status != "binary"],
        "census_only": [f"{f.path} ({'binary' if f.status == 'binary' else f.kind})" for f in kept
                        if f.kind in CENSUS_ONLY_KINDS or f.status == "binary"],
        "skipped": [f"{f.path} (size cap)" for f in skipped],
        "fired": len(fired),
    }
    return Analysis(kept, [x.to_dict() for x in fired], coverage)
