"""Pure: findings → ranked issues, and the cluster summary counts.

Pods of one workload (the name before the replica-set and pod hashes) with the same kind and severity become
one issue with a count and up to three example pods. Ranking: severity, then count (descending), then
namespace and name."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import commands
from detect import Finding
from parse_kubectl import Parsed

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_EXAMPLES = 3
MAX_EVIDENCE = 5
SUMMARY_STATUSES = 3
# Pods of one workload crash-looping together are one issue; a pasted describe that shows OOMKilled for any
# of them makes the whole issue an OOM kill.
_SAME_GROUP = {"oom_killed": "crash_loop"}
POD_KINDS = frozenset({"crash_loop", "oom_killed", "image_pull", "pending_unschedulable", "restart_storm",
                       "not_ready", "evicted_or_failed"})


def issues(findings: Sequence[Finding]) -> list[dict]:
    groups: dict[tuple, list[Finding]] = {}
    for f in findings:
        kind = _SAME_GROUP.get(f.kind, f.kind)
        groups.setdefault((kind, f.severity, f.namespace, f.owner, f.object_type), []).append(f)
    young = {f.replicaset: f.detail for f in findings if f.kind == "recent_rollout"}
    built = [_issue(members, young) for members in groups.values()]
    ranked = sorted(built, key=lambda i: (SEVERITY_ORDER[i["severity"]], -i["count"], i["namespace"], i["object"]))
    return [{**issue, "id": f"i{n}"} for n, issue in enumerate(ranked, start=1)]


def _issue(members: list[Finding], young: dict[str, str]) -> dict:
    # The most telling member leads: an OOM kill over a plain crash loop, then the most restarts.
    members = sorted(members, key=lambda f: (f.kind != "oom_killed", -f.metric, f.name))
    first = members[0]
    is_pod = first.object_type == "pod"
    grouped = is_pod and (len(members) > 1 or first.owner != first.name)
    evidence = list(dict.fromkeys(line for f in members[:MAX_EXAMPLES] for line in f.evidence))[:MAX_EVIDENCE]
    count = first.metric if first.kind == "warning_events" else len(members)
    issue = {
        "kind": first.kind, "severity": first.severity, "namespace": first.namespace,
        "object": first.owner if grouped else first.name or first.owner,
        "object_type": "workload" if grouped else first.object_type,
        "count": count, "examples": [f.name for f in members[:MAX_EXAMPLES] if f.name],
        "evidence": evidence, "detail": first.detail, "explanation": "", "likely_causes": [],
        "flags": sorted({flag for f in members for flag in f.flags}),
        "correlation": _correlation(first.kind, members, young),
    }
    if first.object_type in ("deployment", "statefulset", "daemonset"):
        issue["object_type"] = first.object_type
    return {**issue, "next_command": commands.choose(first.kind, issue)}


def _correlation(kind: str, members: list[Finding], young: dict[str, str]) -> str:
    """A pod issue gets the rollout line only when its pods' replica set is the young one."""
    if kind not in POD_KINDS:
        return ""
    for rs in sorted({f.replicaset for f in members if f.replicaset}):
        detail = young.get(rs, "")
        if " · " in detail:
            return detail.split(" · ", 1)[1]
    return ""


def covered_names(findings: Sequence[Finding]) -> set[str]:
    return {name for f in findings for name in (f.name, f.owner, f.replicaset) if name}


def summary(parsed: Parsed, ranked: Sequence[dict]) -> dict:
    statuses = Counter(p.status for p in parsed.pods.values())
    nodes_ready = sum(1 for n in parsed.nodes.values() if "Ready" in n.status)
    return {
        "pods_total": sum(statuses.values()), "pods_by_status": dict(statuses.most_common()),
        "nodes_ready": nodes_ready, "nodes_total": len(parsed.nodes),
        "namespaces_with_issues": sorted({i["namespace"] for i in ranked if i["namespace"]}),
        "issues_by_severity": {s: sum(1 for i in ranked if i["severity"] == s) for s in SEVERITY_ORDER},
    }


def pods_phrase(counts: dict) -> str:
    """`148 pods (139 Running, 4 CrashLoopBackOff, 3 Pending, 2 other)`."""
    by_status = counts["pods_by_status"]
    if not counts["pods_total"]:
        return ""
    shown = [("Running", by_status.get("Running", 0))] if by_status.get("Running") else []
    rest = [(s, n) for s, n in by_status.items() if s != "Running"]
    shown += rest[:SUMMARY_STATUSES]
    other = sum(n for _, n in rest[SUMMARY_STATUSES:])
    parts = [f"{n} {s}" for s, n in shown] + ([f"{other} other"] if other else [])
    return f"{counts['pods_total']} pods ({', '.join(parts)})"


def nodes_phrase(counts: dict) -> str:
    return f"{counts['nodes_ready']}/{counts['nodes_total']} nodes Ready" if counts["nodes_total"] else ""
