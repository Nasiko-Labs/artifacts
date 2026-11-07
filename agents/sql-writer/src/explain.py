"""Pure: read pasted EXPLAIN output (PostgreSQL text format, MySQL tabular) into X findings, each carrying the
verbatim plan line as evidence. Thresholds are fixed here; every number comes from the plan text itself."""

from __future__ import annotations

import re
from dataclasses import dataclass

from findings import Finding

BIG_SCAN_ROWS = 100_000
FILTERED_SHARE = 0.9
REMOVED_MIN = 10_000
MISMATCH_FACTOR = 10
UNRECOGNISED = "EXPLAIN format not recognised, read as text: no plan findings."

_NODE = re.compile(r"^(?P<indent>\s*)(?:->\s*)?(?P<op>[A-Z][A-Za-z ]+?)(?: on (?P<table>[\w.\"]+)(?: (?P<alias>\w+))?)?"
                   r"\s+\(cost=[\d.]+\.\.[\d.]+ rows=(?P<est>\d+)[^)]*\)(?:\s+\(actual time=[\d.]+\.\.[\d.]+ "
                   r"rows=(?P<act>\d+) loops=(?P<loops>\d+)\))?")
_REMOVED = re.compile(r"Rows Removed by (?:Join )?Filter: (\d+)")
_FILTER = re.compile(r"^\s*(?:Filter|Index Cond|Recheck Cond): (.+)$")
_DISK_SORT = re.compile(r"Sort Method: external (?:merge|sort)\s+Disk: (\d+)kB")
_BUFFERS = re.compile(r"Buffers: shared(?: hit=(\d+))?(?: read=(\d+))?")
_MYSQL_HEAD = re.compile(r"^\s*\|.*\bselect_type\b.*\|\s*$", re.IGNORECASE)


@dataclass
class _Node:
    indent: int
    op: str
    table: str
    est: int
    actual: int | None        # rows per loop, as the plan prints them
    loops: int
    line: str
    removed: int = 0
    filter: str = ""


def read(plan: str) -> tuple[list[Finding], str]:
    """(findings, note). The note is empty when the format was recognised."""
    if not plan.strip():
        return [], ""
    lines = plan.splitlines()
    if any(_MYSQL_HEAD.match(ln) for ln in lines):
        return _mysql(lines), ""
    nodes = _postgres_nodes(lines)
    if not nodes:
        return [], UNRECOGNISED
    found = []
    for i, node in enumerate(nodes):
        found += _node_findings(node, nodes[:i])
    found += _worst_mismatch(nodes)
    found += [Finding("", "X02", ln.strip(), f"The sort spilled {m.group(1)} kB to disk; more work_mem or an index "
                      "matching the ORDER BY avoids it.") for ln in lines if (m := _DISK_SORT.search(ln))]
    found += _buffers(lines)
    return found, ""


def _postgres_nodes(lines: list[str]) -> list[_Node]:
    nodes: list[_Node] = []
    for line in lines:
        m = _NODE.match(line)
        if m:
            act = int(m.group("act")) if m.group("act") else None
            nodes.append(_Node(len(m.group("indent")), m.group("op").strip(), (m.group("table") or "").strip('"'),
                               int(m.group("est")), act, int(m.group("loops") or 1), line.strip()))
        elif nodes and (r := _REMOVED.search(line)):
            nodes[-1].removed += int(r.group(1))
        elif nodes and (f := _FILTER.match(line)):
            nodes[-1].filter = f.group(1).strip()
    return nodes


def _node_findings(node: _Node, before: list[_Node]) -> list[Finding]:
    out = []
    scanned = (node.actual if node.actual is not None else node.est) + node.removed
    share = node.removed / scanned if scanned else 0.0
    cols = filter_columns(node.filter)
    if node.op == "Seq Scan" and scanned >= BIG_SCAN_ROWS and (share >= FILTERED_SHARE or not node.filter):
        kept = f"; {share:.0%} of them were filtered out" if node.removed else ""
        out.append(Finding("", "X01", node.line, f"Sequential scan of `{node.table}` over {scanned:,} rows{kept}. "
                           "An index on the filtered columns lets the database skip most of them.", node.table,
                           cols[:2]))
    elif node.op != "Seq Scan" and node.removed >= REMOVED_MIN and share >= FILTERED_SHARE:
        out.append(Finding("", "X04", node.line, f"{node.removed:,} of {scanned:,} rows read here were thrown "
                           f"away by the filter ({share:.0%}).", node.table, cols[:2]))
    if node.op == "Seq Scan" and before and _is_inner_of_nested_loop(node, before):
        out.append(Finding("", "X03", node.line, f"The inner side of a nested loop scans `{node.table}` "
                           f"sequentially, {node.loops:,} times; an index on the join column fixes it.", node.table,
                           cols[:1]))
    return out


def _is_inner_of_nested_loop(node: _Node, before: list[_Node]) -> bool:
    """True when the node is the second child of a Nested Loop (an earlier sibling sits at its indent)."""
    idx = next((i for i in range(len(before) - 1, -1, -1) if before[i].indent < node.indent), None)
    if idx is None or before[idx].op != "Nested Loop":
        return False
    return any(n.indent == node.indent for n in before[idx + 1:])


def _worst_mismatch(nodes: list[_Node]) -> list[Finding]:
    """One X05 at most: the node whose estimate is furthest from the actual rows (both per loop)."""
    scored = [(max(n.actual, n.est) / min(n.actual, n.est), n) for n in nodes
              if n.actual is not None and min(n.actual, n.est) > 0]
    if not scored:
        return []
    ratio, node = max(scored, key=lambda pair: pair[0])
    if ratio < MISMATCH_FACTOR:
        return []
    return [Finding("", "X05", node.line, f"The planner estimated {node.est:,} rows but got {node.actual:,} "
                    f"({ratio:.0f}x off); stale statistics mislead join and scan choices. Run ANALYZE on "
                    f"`{node.table or 'the table'}`.", node.table)]


_NOT_COLUMN = frozenset(re.findall(r"\w+", (
    "date text integer int numeric timestamp timestamptz without with time zone and or not null is any "
    "like ilike lower upper bigint varchar character varying true false"
)))


def filter_columns(condition: str) -> tuple[str, ...]:
    """Column names in a plan Filter / Index Cond line: identifiers that are not functions, types or literals."""
    plain = re.sub(r"'[^']*'", " ", condition)
    names = [w.rsplit(".", 1)[-1] for w in re.findall(r"\b([a-z_][\w.]*)\b(?!\s*\()", plain, re.IGNORECASE)]
    return tuple(dict.fromkeys(n for n in names if n.casefold() not in _NOT_COLUMN))[:2]


def _buffers(lines: list[str]) -> list[Finding]:
    for line in lines:
        m = _BUFFERS.search(line)
        if m and m.group(2) and int(m.group(2)) > int(m.group(1) or 0):
            return [Finding("", "X06", line.strip(), f"{int(m.group(2)):,} blocks came from disk against "
                            f"{int(m.group(1) or 0):,} from cache; the working set doesn't fit in memory.")]
    return []


def _mysql(lines: list[str]) -> list[Finding]:
    header = next(ln for ln in lines if _MYSQL_HEAD.match(ln))
    names = [c.strip().casefold() for c in header.strip().strip("|").split("|")]
    out = []
    for line in lines:
        if not line.strip().startswith("|") or line is header:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        row = dict(zip(names, cells))
        if row.get("type", "").upper() == "ALL":
            rows = row.get("rows", "?")
            out.append(Finding("", "X01", line.strip(), f"Full table scan of `{row.get('table', '?')}` "
                               f"(type=ALL, about {rows} rows); no index was used.", row.get("table", "")))
        if "filesort" in row.get("extra", "").casefold():
            out.append(Finding("", "X02", line.strip(), "MySQL sorts these rows itself (Using filesort); an index "
                               "matching the ORDER BY avoids it."))
    return out
