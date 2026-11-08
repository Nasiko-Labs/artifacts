"""Pure: node, resource, rollout and warning-event findings. Thresholds live in detect.py."""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median

from detect import (
    HEAVY_FACTOR,
    PRESSURE_HIGH_PCT,
    PRESSURE_PCT,
    RECENT_ROLLOUT_H,
    Finding,
)
from parse_kubectl import Parsed

PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure")
MIN_PODS_FOR_MEDIAN = 3
MAX_EVENT_GROUPS = 10


def node_findings(parsed: Parsed) -> list[Finding]:
    out: list[Finding] = []
    for node in parsed.nodes.values():
        if "NotReady" in node.status or "Unknown" in node.status:
            out.append(Finding("node_not_ready", "critical", "", node.name, "node", ",".join(node.status),
                               (node.line,), node.name))
        elif "SchedulingDisabled" in node.status:
            out.append(Finding("node_not_ready", "low", "", node.name, "node", "cordoned (SchedulingDisabled)",
                               (node.line,), node.name))
    flagged = {f.owner for f in out if f.severity == "critical"}
    for detail in parsed.node_details.values():
        ready = detail.conditions.get("Ready")
        if ready and ready[0] in ("False", "Unknown") and detail.name not in flagged:
            out.append(Finding("node_not_ready", "critical", "", detail.name, "node", f"Ready {ready[0]}",
                               (ready[1],), detail.name))
        for condition in PRESSURE_CONDITIONS:
            status = detail.conditions.get(condition)
            if status and status[0] == "True":
                out.append(Finding("node_pressure", "high", "", detail.name, "node", f"{condition} True",
                                   (status[1],), detail.name))
    return out


def usage_findings(parsed: Parsed) -> list[Finding]:
    out: list[Finding] = []
    for node in parsed.top_nodes:
        worst = max((p for p in (node.cpu_pct, node.mem_pct) if p is not None), default=None)
        if worst is None or worst < PRESSURE_PCT:
            continue
        parts = [f"{label} {pct}%" for label, pct in (("CPU", node.cpu_pct), ("memory", node.mem_pct))
                 if pct is not None and pct >= PRESSURE_PCT]
        severity = "high" if worst >= PRESSURE_HIGH_PCT else "medium"
        out.append(Finding("resource_pressure", severity, "", node.name, "node", " · ".join(parts), (node.line,),
                           node.name))
    return out + _heavy_pods(parsed)


def _heavy_pods(parsed: Parsed) -> list[Finding]:
    by_ns: dict[str, list] = {}
    for pod in parsed.top_pods:
        if pod.mem_mib is not None:
            by_ns.setdefault(pod.namespace, []).append(pod)
    out = []
    for ns, pods in sorted(by_ns.items()):
        if len(pods) < MIN_PODS_FOR_MEDIAN:
            continue
        mid = median(p.mem_mib for p in pods)
        heavy = sorted((p for p in pods if mid > 0 and p.mem_mib > HEAVY_FACTOR * mid), key=lambda p: -p.mem_mib)
        for pod in heavy:
            detail = f"heavy pod: memory over {HEAVY_FACTOR}x the namespace median ({round(mid)}Mi)"
            out.append(Finding("resource_pressure", "low", ns, pod.name, "pod", detail, (pod.line,), pod.name))
    return out


def rollout_findings(parsed: Parsed, failing: Iterable[Finding]) -> list[Finding]:
    out: list[Finding] = []
    for w in parsed.workloads:
        if w.kind == "replicaset" or w.desired == 0 or w.ready >= w.desired:
            continue
        text = f"ready {w.ready}/{w.desired}"
        if w.up_to_date is not None and w.up_to_date < w.desired:
            text += " · rollout in progress"
        out.append(Finding("rollout_unhealthy", "high", w.namespace, w.name, w.kind, text, (w.line,), w.name))
    failing_rs = {f.replicaset for f in failing if f.replicaset}
    for rs in _newest_recent_rs(parsed):
        deploy = rs.name.rpartition("-")[0]
        text = f"rollout {rs.name} {rs.age_text} old"
        if rs.name in failing_rs:
            text += f" · pods of {deploy} began failing after rollout {rs.name} (age {rs.age_text})"
        out.append(Finding("recent_rollout", "low", rs.namespace, deploy, "deployment", text, (rs.line,), deploy,
                           replicaset=rs.name))
    return out


def _newest_recent_rs(parsed: Parsed) -> list:
    newest: dict[tuple[str, str], object] = {}
    for w in parsed.workloads:
        if w.kind != "replicaset" or w.desired == 0 or w.age is None or w.age >= RECENT_ROLLOUT_H * 60:
            continue
        key = (w.namespace, w.name.rpartition("-")[0])
        if key not in newest or w.age < newest[key].age:
            newest[key] = w
    return list(newest.values())


def event_findings(parsed: Parsed, covered: set[str]) -> list[Finding]:
    """Warning events grouped by (namespace, reason, object), for objects no other issue covers."""
    groups: dict[tuple[str, str, str], list] = {}
    for event in parsed.events:
        if event.type != "Warning" or event.object in covered:
            continue
        groups.setdefault((event.namespace, event.reason, event.object), []).append(event)
    ranked = sorted(groups.items(), key=lambda kv: (-sum(e.count for e in kv[1]), kv[0]))[:MAX_EVENT_GROUPS]
    out = []
    for (ns, reason, obj), events in ranked:
        count = sum(e.count for e in events)
        kind = events[0].object_kind or "object"
        out.append(Finding("warning_events", "low", ns, obj, kind, f"{reason} x {count}", (events[0].line,), obj,
                           metric=count))
    return out
