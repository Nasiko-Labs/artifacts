"""Pure: the fixed, read-only kubectl templates. Nothing else is ever suggested.

A template is filled only with names that came from the parsed output and pass the Kubernetes name rule;
a name that does not is never put in a command. The model may only pick a template id; code fills it."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")

TEMPLATES: Mapping[str, str] = {
    "logs_previous": "kubectl logs {pod} -n {ns} --previous",
    "describe_pod": "kubectl describe pod {pod} -n {ns}",
    "events_ns": "kubectl get events -n {ns} --sort-by=.lastTimestamp",
    "describe_node": "kubectl describe node {node}",
    "top_nodes": "kubectl top nodes",
    "top_pods": "kubectl top pods -n {ns}",
    "rollout_history": "kubectl rollout history {wtype}/{deploy} -n {ns}",
    "get_rs": "kubectl get rs -n {ns}",
}

# Per issue kind: the templates that fit, the first being the default.
ALLOWED: Mapping[str, tuple[str, ...]] = {
    "crash_loop": ("logs_previous", "describe_pod", "events_ns"),
    "oom_killed": ("logs_previous", "describe_pod", "top_pods"),
    "image_pull": ("describe_pod", "events_ns"),
    "pending_unschedulable": ("describe_pod", "events_ns", "top_nodes"),
    "restart_storm": ("logs_previous", "describe_pod", "events_ns"),
    "not_ready": ("describe_pod", "logs_previous", "events_ns"),
    "evicted_or_failed": ("describe_pod", "events_ns", "top_nodes"),
    "node_not_ready": ("describe_node", "top_nodes"),
    "node_pressure": ("describe_node", "top_nodes"),
    "resource_pressure": ("describe_node", "top_pods", "top_nodes"),
    "rollout_unhealthy": ("rollout_history", "get_rs", "events_ns"),
    "recent_rollout": ("rollout_history", "get_rs"),
    "warning_events": ("events_ns",),
}

BASE_COMMANDS = (
    "kubectl get pods -A -o wide",
    "kubectl get nodes",
    "kubectl get events -A --sort-by=.lastTimestamp",
    "kubectl top nodes",
    "kubectl get deploy -A",
    "kubectl get rs -A",
)
_WORKLOADS = ("deployment", "statefulset", "daemonset")
_BLOCK_FOR_BASE = ("pods", "nodes", "events", "top_nodes", "deployments", "replicasets")


def safe(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def fill(template_id: str, issue: Mapping) -> str | None:
    """The filled command, or None when a name it needs is missing or unsafe."""
    template = TEMPLATES.get(template_id)
    if template is None:
        return None
    examples = list(issue.get("examples") or [])
    values = {"ns": issue.get("namespace") or "", "deploy": issue.get("object") or "",
              "pod": examples[0] if examples else issue.get("object") or "",
              "node": issue.get("object") or "",
              "wtype": issue.get("object_type") if issue.get("object_type") in _WORKLOADS else "deployment"}
    needed = re.findall(r"\{(\w+)\}", template)
    if any(not safe(values[key]) for key in needed):
        return None
    return template.format(**values)


def choose(kind: str, issue: Mapping, wanted: str | None = None) -> str:
    """The model's pick when allowed for this kind and fillable, else the first allowed one that fills."""
    allowed = ALLOWED.get(kind, ())
    order = ([wanted] if wanted in allowed else []) + [a for a in allowed if a != wanted]
    for template_id in order:
        command = fill(template_id, issue)
        if command:
            return command
    return "kubectl get events -A --sort-by=.lastTimestamp"


def what_next(issues: Iterable[Mapping], present: Iterable[str]) -> list[str]:
    """What to paste next: describe/logs for flagged pods without a describe, then missing base blocks."""
    have = set(present)
    out: list[str] = []
    for issue in issues:
        flags = issue.get("flags") or []
        if issue.get("kind") in ("crash_loop", "oom_killed", "restart_storm") and issue.get("examples"):
            out.append(fill("logs_previous", issue) or "")
        if "no_describe" in flags or "no_events" in flags or issue.get("kind") in ("image_pull", "not_ready"):
            out.append(fill("describe_pod", issue) or "")
        if issue.get("kind") in ("node_not_ready", "node_pressure") and "describe_node" not in have:
            out.append(fill("describe_node", issue) or "")
    out += [cmd for cmd, block in zip(BASE_COMMANDS, _BLOCK_FOR_BASE, strict=True) if block not in have]
    return list(dict.fromkeys(c for c in out if c))[:10]
