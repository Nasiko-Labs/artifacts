"""Pure: blocks → the full result (issues, summary counts, coverage), all in code. The explain step adds
words afterwards; nothing here calls a model."""

from __future__ import annotations

from collections.abc import Sequence

import detect
import detect_cluster
import group
from intake import Block
from parse_kubectl import parse

LABELS = {
    "pods": "get pods", "nodes": "get nodes", "deployments": "get deploy", "replicasets": "get rs",
    "statefulsets": "get sts", "daemonsets": "get ds", "events": "get events", "top_nodes": "top nodes",
    "top_pods": "top pods", "rollout_history": "rollout history", "describe_pod": "describe pod",
    "describe_node": "describe node",
}
_MISSING = (
    (("pods",), "no `get pods` output pasted: pod health not assessed"),
    (("nodes", "describe_node"), "no `get nodes` output pasted: node readiness not assessed"),
    (("events", "describe_pod"), ("no `get events` output pasted: scheduling and image pull failures have no "
                                  "event evidence")),
    (("top_nodes", "top_pods"), "no `top` output pasted: resource pressure not assessed"),
    (("deployments", "replicasets", "statefulsets", "daemonsets"),
     "no `get deploy` / `get rs` output pasted: rollouts not assessed"),
)
THRESHOLDS = {"restart_storm": detect.RESTART_STORM, "recent_restart_min": detect.RECENT_RESTART_MIN,
              "pending_min": detect.PENDING_MIN, "pressure_pct": detect.PRESSURE_PCT,
              "recent_rollout_h": detect.RECENT_ROLLOUT_H}


def run(blocks: Sequence[Block], unparsed: Sequence[Block] = (), label: str = "") -> dict:
    parsed = parse(blocks)
    pods = detect.pod_findings(parsed)
    others = detect_cluster.node_findings(parsed) + detect_cluster.usage_findings(parsed)
    rollouts = detect_cluster.rollout_findings(parsed, pods)
    covered = group.covered_names([*pods, *others, *rollouts])
    events = detect_cluster.event_findings(parsed, covered)
    ranked = group.issues([*pods, *others, *rollouts, *events])
    present = sorted(set(parsed.types))
    return {
        "label": label, "issues": ranked, "counts": group.summary(parsed, ranked),
        "present": present,
        "parsed_blocks": [{"type": b.type, "label": _block_label(b), "rows": parsed.rows.get(b.type, 0)}
                          for b in blocks],
        "unparsed_blocks": [_unparsed(n, b) for n, b in enumerate(unparsed, start=1)],
        "not_assessed": [text for types, text in _MISSING if not set(types) & set(present)],
        "skipped_rows": parsed.skipped, "thresholds": THRESHOLDS,
    }


def _block_label(block: Block) -> str:
    return f"kubectl {block.label}" if block.label else LABELS.get(block.type or "", "unknown")


def _unparsed(n: int, block: Block) -> str:
    start = block.first_line()[:60].replace("`", "'")
    label = f"`kubectl {block.label}`" if block.label else f"block {n}"
    return f"{label} not recognised (starts `{start}`): not parsed"


def confidence(result: dict) -> str:
    """high: pods + nodes + events pasted and every block parsed; low: one block, or something unparsed;
    medium otherwise."""
    present = set(result["present"])
    core = sum(1 for group_types in (("pods",), ("nodes", "describe_node"), ("events", "describe_pod"))
               if set(group_types) & present)
    if result["unparsed_blocks"] or len(result["parsed_blocks"]) <= 1:
        return "low"
    return "high" if core == 3 else "medium"


def needs_human(result: dict) -> bool:
    return any(issue["severity"] == "critical" for issue in result["issues"])
