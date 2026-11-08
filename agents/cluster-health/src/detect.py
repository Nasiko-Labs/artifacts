"""Pure: pod findings from the parsed tables, describe blocks and events. One finding per pod, the most
severe rule that fires. Thresholds are the constants below, shown on the card."""

from __future__ import annotations

import re
from dataclasses import dataclass

import tables as t
from parse_kubectl import Event, Parsed, Pod

RESTART_STORM = 5          # restarts
RECENT_RESTART_MIN = 60    # minutes since the last restart
PENDING_MIN = 10           # minutes Pending before it counts
PRESSURE_PCT = 85          # node CPU or memory %
PRESSURE_HIGH_PCT = 95
RECENT_ROLLOUT_H = 24      # hours
LOOP_RESTARTS = 3          # Error / RunContainerError with this many restarts is a crash loop
STUCK_TERMINATING_MIN = 30
HEAVY_FACTOR = 3           # top pods: memory over this × the namespace median

IMAGE_PULL = frozenset({"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"})
FAILED = frozenset({"Evicted", "Failed", "Error", "OOMKilled", "ContainerStatusUnknown", "Terminating"})
_DEPLOY_POD = re.compile(r"^(?P<owner>.+)-(?P<hash>[a-z0-9]{6,10})-[a-z0-9]{5}$")
_SAFE_HASH = re.compile(r"^[bcdfghjklmnpqrstvwxz2-9]+$")   # the alphabet Kubernetes draws name hashes from
_ORDINAL_POD = re.compile(r"^(?P<owner>.+)-\d+$")
_SUFFIX_POD = re.compile(r"^(?P<owner>.+)-[a-z0-9]{5}$")


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    namespace: str
    owner: str               # what the issue is about: a workload, a node, or the pod itself
    object_type: str         # pod | node | deployment | statefulset | daemonset | replicaset | namespace
    detail: str
    evidence: tuple[str, ...]
    name: str = ""           # the pod / node / rs this finding is about
    flags: tuple[str, ...] = ()
    replicaset: str = ""
    metric: int = 0          # restarts for a pod, event count for warning events


def owner_of(pod_name: str) -> tuple[str, str]:
    """(owner, replica set) from a pod name: `payments-api-7d9c8b6f5-xk2lm` → (`payments-api`,
    `payments-api-7d9c8b6f5`); `indexer-0` → (`indexer`, ``); anything else → itself."""
    for pattern in (_DEPLOY_POD, _ORDINAL_POD, _SUFFIX_POD):
        match = pattern.match(pod_name)
        if match and "hash" in pattern.groupindex and not _looks_like_hash(match.group("hash")):
            continue
        if match:
            rs = f"{match.group('owner')}-{match.group('hash')}" if "hash" in pattern.groupindex else ""
            return match.group("owner"), rs
    return pod_name, ""


def _looks_like_hash(text: str) -> bool:
    """A replica-set hash, not a word of the name: it has a digit, or only Kubernetes' vowel-free alphabet."""
    return any(ch.isdigit() for ch in text) or bool(_SAFE_HASH.match(text))


def pods_with_details(parsed: Parsed) -> list[Pod]:
    """Every pod from `get pods`, plus a pod known only from a pasted `describe`."""
    pods = dict(parsed.pods)
    for key, detail in parsed.pod_details.items():
        if key in pods or not detail.name:
            continue
        waiting = next((c.state_reason for c in detail.containers if c.state_reason), "")
        restarts = max((c.restart_count for c in detail.containers), default=0)
        pods[key] = Pod(detail.namespace, detail.name, None, waiting or detail.status, restarts, None, None, "",
                        detail.name_line)
    return list(pods.values())


def pod_findings(parsed: Parsed) -> list[Finding]:
    events = _events_by_object(parsed.events)
    out = []
    for pod in pods_with_details(parsed):
        finding = _pod_finding(parsed, pod, events.get((pod.namespace, pod.name), []))
        if finding is not None:
            out.append(finding)
    return out


def _events_by_object(events: list[Event]) -> dict[tuple[str, str], list[Event]]:
    out: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        if event.object_kind in ("pod", ""):
            out.setdefault((event.namespace, event.object), []).append(event)
    return out


def _pod_finding(parsed: Parsed, pod: Pod, events: list[Event]) -> Finding | None:
    status = pod.status.removeprefix("Init:")
    detail = parsed.pod_details.get((pod.namespace, pod.name))
    has_events = bool(events) or bool(detail and detail.events)
    rule = _rule(pod, status)
    if rule is None:
        return None
    kind, severity, text = rule
    evidence = [pod.line]
    flags = []
    if kind == "crash_loop":
        last = detail.last_terminated() if detail else None
        if last is not None:
            kind = "oom_killed" if last.last_reason == "OOMKilled" else kind
            exit_part = f" (exit {last.exit_code})" if last.exit_code else ""
            text += f" · last state {last.last_reason}{exit_part}"
            evidence += [ln for ln in last.evidence if ln.startswith(("Reason:", "Exit Code:"))]
        else:
            flags.append("no_describe")
    if kind == "pending_unschedulable":
        quote = _event_quote(events, detail, ("FailedScheduling",), ())
        text += f" > {quote[0]}" if quote else ", no events pasted"
        evidence += [quote[1]] if quote else []
    if kind == "image_pull":
        quote = _event_quote(events, detail, ("Failed", "ErrImagePull", "BackOff"), ("pull",))
        evidence += [quote[1]] if quote else []
    if not has_events and kind in ("pending_unschedulable", "image_pull", "crash_loop", "oom_killed"):
        flags.append("no_events")
    owner, rs = owner_of(pod.name)
    return Finding(kind, severity, pod.namespace, owner, "pod", text, tuple(dict.fromkeys(evidence)), pod.name,
                   tuple(flags), rs, pod.restarts)


def _rule(pod: Pod, status: str) -> tuple[str, str, str] | None:
    looping = status == "CrashLoopBackOff" or (status in ("Error", "RunContainerError", "OOMKilled")
                                               and pod.restarts >= LOOP_RESTARTS)
    if looping:
        kind = "oom_killed" if status == "OOMKilled" else "crash_loop"
        return kind, "critical", f"{pod.status} · restarts {pod.restarts}"
    if status in IMAGE_PULL:
        return "image_pull", "high", pod.status
    if status == "Pending" and pod.age is not None and pod.age >= PENDING_MIN:
        return "pending_unschedulable", "high", f"Pending {t.fmt_minutes(pod.age)}"
    if pod.restarts >= RESTART_STORM and status != "Completed":
        recent = pod.restart_ago is not None and pod.restart_ago <= RECENT_RESTART_MIN
        text = f"restarts {pod.restarts}" + ("" if recent else " · high restart count, not recent")
        return "restart_storm", "high" if recent else "medium", text
    if status == "Terminating" and (pod.age or 0) <= STUCK_TERMINATING_MIN:
        return None
    if status in FAILED:
        return "evicted_or_failed", "medium", pod.status
    if status == "Running" and pod.ready and pod.ready[0] < pod.ready[1]:
        return "not_ready", "medium", f"Running but ready {pod.ready[0]}/{pod.ready[1]}"
    return None


def _event_quote(events: list[Event], detail, reasons: tuple[str, ...],
                 words: tuple[str, ...]) -> tuple[str, str] | None:
    """(`Reason: message`, verbatim line) of the first matching event (table first, then describe)."""
    candidates = [(e.reason, e.message, e.line) for e in events]
    candidates += [(e.reason, e.message, e.line) for e in (detail.events if detail else [])]
    for reason, message, line in candidates:
        if reason in reasons and all(w in message.casefold() for w in words):
            return f"{reason}: {message}", line
    return None
