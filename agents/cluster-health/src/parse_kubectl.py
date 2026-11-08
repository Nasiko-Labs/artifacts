"""Pure: recognised blocks → typed records. Every record keeps its verbatim source line as evidence.

Tables come through tables.parse_rows; `describe pod|node` through describe.py. A namespace missing from a
table (no `-A`) comes from the command's `-n`, else `default`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import describe
import tables as t
from intake import Block


@dataclass(frozen=True)
class Pod:
    namespace: str
    name: str
    ready: tuple[int, int] | None
    status: str
    restarts: int
    restart_ago: float | None
    age: float | None
    node: str
    line: str
    extra: tuple[str, ...] = ()   # describe lines that add evidence


@dataclass(frozen=True)
class Node:
    name: str
    status: tuple[str, ...]
    line: str


@dataclass(frozen=True)
class Workload:
    kind: str                     # deployment | statefulset | daemonset | replicaset
    namespace: str
    name: str
    ready: int
    desired: int
    up_to_date: int | None
    age: float | None
    age_text: str
    line: str


@dataclass(frozen=True)
class Event:
    namespace: str
    type: str
    reason: str
    object: str                   # `pod/indexer-0` → `indexer-0`
    object_kind: str
    message: str
    count: int
    line: str


@dataclass(frozen=True)
class Usage:
    namespace: str
    name: str
    cpu_pct: int | None
    mem_pct: int | None
    mem_mib: float | None
    line: str


@dataclass
class Parsed:
    pods: dict[tuple[str, str], Pod] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    workloads: list[Workload] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    top_nodes: list[Usage] = field(default_factory=list)
    top_pods: list[Usage] = field(default_factory=list)
    pod_details: dict[tuple[str, str], describe.PodDetail] = field(default_factory=dict)
    node_details: dict[str, describe.NodeDetail] = field(default_factory=dict)
    types: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    skipped: int = 0


def parse(blocks: Sequence[Block]) -> Parsed:
    out = Parsed()
    for block in blocks:
        out.types.append(block.type)
        if block.type == "describe_pod":
            detail = describe.pod(block.lines)
            out.pod_details[(detail.namespace, detail.name)] = detail
            out.rows[block.type] = out.rows.get(block.type, 0) + 1
            continue
        if block.type == "describe_node":
            node = describe.node(block.lines)
            out.node_details[node.name] = node
            out.rows[block.type] = out.rows.get(block.type, 0) + 1
            continue
        rows, skipped = t.parse_rows(block.header, block.lines) if block.header else ([], 0)
        out.skipped += skipped
        out.rows[block.type] = out.rows.get(block.type, 0) + len(rows)
        _TABLES.get(block.type, _ignore)(out, rows, block.namespace or "default")
    return out


def _ignore(out: Parsed, rows: list[t.Row], ns: str) -> None:
    return None


def _pods(out: Parsed, rows: list[t.Row], ns: str) -> None:
    for row in rows:
        count, ago = t.restarts(row.get("RESTARTS"))
        pod = Pod(row.get("NAMESPACE") or ns, row.get("NAME"), t.ratio(row.get("READY")), row.get("STATUS"),
                  count, ago, t.age_minutes(row.get("AGE")), row.get("NODE"), row.line)
        out.pods[(pod.namespace, pod.name)] = pod


def _nodes(out: Parsed, rows: list[t.Row], ns: str) -> None:
    for row in rows:
        out.nodes[row.get("NAME")] = Node(row.get("NAME"), tuple(row.get("STATUS").split(",")), row.line)


def _workloads(kind: str):
    def parse_rows(out: Parsed, rows: list[t.Row], ns: str) -> None:
        for row in rows:
            ready = t.ratio(row.get("READY"))
            if kind == "daemonset" or kind == "replicaset":
                desired, ready_n = t.as_int(row.get("DESIRED")), t.as_int(row.get("READY"))
            else:
                desired, ready_n = (ready[1], ready[0]) if ready else (None, None)
            if desired is None or ready_n is None:
                continue
            out.workloads.append(Workload(kind, row.get("NAMESPACE") or ns, row.get("NAME"), ready_n, desired,
                                          t.as_int(row.get("UP-TO-DATE")), t.age_minutes(row.get("AGE")),
                                          row.get("AGE"), row.line))
    return parse_rows


def _events(out: Parsed, rows: list[t.Row], ns: str) -> None:
    for row in rows:
        kind, _, name = row.get("OBJECT").rpartition("/")
        count = t.as_int(row.get("COUNT")) or t.times(row.get("LAST SEEN"))
        out.events.append(Event(row.get("NAMESPACE") or ns, row.get("TYPE"), row.get("REASON"), name,
                                kind.split(".")[0].casefold(), row.get("MESSAGE"), count, row.line))


def _top_nodes(out: Parsed, rows: list[t.Row], ns: str) -> None:
    for row in rows:
        cpu = t.percent(row.get("CPU%") or row.get("CPU(%)"))
        mem = t.percent(row.get("MEMORY%") or row.get("MEMORY(%)"))
        out.top_nodes.append(Usage("", row.get("NAME"), cpu, mem, t.memory_mib(row.get("MEMORY(BYTES)")), row.line))


def _top_pods(out: Parsed, rows: list[t.Row], ns: str) -> None:
    for row in rows:
        out.top_pods.append(Usage(row.get("NAMESPACE") or ns, row.get("NAME"), None, None,
                                  t.memory_mib(row.get("MEMORY(BYTES)")), row.line))


_TABLES = {
    "pods": _pods, "nodes": _nodes, "events": _events, "top_nodes": _top_nodes, "top_pods": _top_pods,
    "deployments": _workloads("deployment"), "statefulsets": _workloads("statefulset"),
    "daemonsets": _workloads("daemonset"), "replicasets": _workloads("replicaset"),
}
