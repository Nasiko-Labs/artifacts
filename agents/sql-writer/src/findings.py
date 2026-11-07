"""Pure: one lint or EXPLAIN finding, with the verbatim evidence it was raised on."""

from __future__ import annotations

from dataclasses import asdict, dataclass

RULES = {
    "Q01": "SELECT *", "Q02": "non-sargable predicate", "Q03": "leading wildcard LIKE",
    "Q04": "NOT IN with a nullable column", "Q05": "join without a predicate", "Q06": "OR across columns",
    "Q07": "DISTINCT over joins", "Q08": "correlated subquery in SELECT", "Q09": "deep OFFSET",
    "Q10": "UNION where UNION ALL fits", "Q11": "type mismatch with a literal", "Q12": "join keys of different types",
    "Q13": "aggregate after a fan-out join", "Q14": "no partition filter", "Q15": "exact COUNT(DISTINCT)",
    "Q16": "ORDER BY without LIMIT in a subquery", "Q17": "no index covers the filter",
    "X01": "large sequential scan", "X02": "sort spilled to disk", "X03": "nested loop over a sequential scan",
    "X04": "most scanned rows filtered out", "X05": "row estimate far off", "X06": "reads mostly from disk",
}


@dataclass(frozen=True)
class Finding:
    id: str                       # local id s1, s2 ... (set when listed)
    rule: str                     # Q01..Q17, X01..X06
    evidence: str                 # verbatim fragment of the SQL or the EXPLAIN line
    note: str                     # what it means and the usual fix
    table: str = ""               # for index advice
    columns: tuple[str, ...] = ()
    functional: str = ""          # an expression for a functional index (Q02)

    @property
    def title(self) -> str:
        return RULES.get(self.rule, self.rule)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["columns"] = list(self.columns)
        data["title"] = self.title
        return data

    @staticmethod
    def from_dict(data: dict) -> Finding:
        return Finding(data["id"], data["rule"], data["evidence"], data["note"], data.get("table", ""),
                       tuple(data.get("columns") or ()), data.get("functional", ""))


def numbered(findings: list[Finding]) -> list[Finding]:
    """Deduplicated by (rule, evidence) and given ids s1.. in order."""
    seen, out = set(), []
    for f in findings:
        key = (f.rule, f.evidence)
        if key in seen:
            continue
        seen.add(key)
        out.append(Finding(f"s{len(out) + 1}", f.rule, f.evidence, f.note, f.table, f.columns, f.functional))
    return out
