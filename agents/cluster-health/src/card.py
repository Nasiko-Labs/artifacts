"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "cluster-health"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Read-only Kubernetes triage from kubectl output you paste (get pods -A, get nodes, get events -A, top "
    "nodes/pods, get deploy/rs/sts/ds, describe pod/node, rollout history). It flags CrashLoopBackOff and "
    "OOMKilled pods, image pull failures, Pending pods with their FailedScheduling reason, restart storms, "
    "not-ready pods, evictions, NotReady or pressured nodes, CPU/memory pressure and unhealthy or recent "
    "rollouts, each with the verbatim evidence line, a ranked severity and a suggested read-only next command. "
    "Thresholds are fixed in code (restart storm 5 restarts within 60m, Pending 10m, pressure 85%, recent "
    "rollout 24h) and every count is computed in code. It can also read a read-only cluster connector if you "
    "enabled one. It never restarts, deletes, scales or changes anything and answers nothing outside "
    "cluster health."
)

SKILL_ID = "cluster-health-triage"
SKILL_NAME = "Cluster health triage"
SKILL_DESCRIPTION = (
    "Ranked issues from pasted kubectl output: crash loops, OOM kills, image pulls, unschedulable pods, "
    "restart storms, node and resource pressure, unhealthy rollouts, each with evidence and a read-only next "
    "command."
)
SKILL_TAGS = ["kubernetes", "kubectl", "sre", "cluster health", "triage", "read-only", "platform"]
SKILL_EXAMPLES = [
    "Here is kubectl get pods -A: what is unhealthy? <paste>",
    "Explain the CrashLoop in payments (paste kubectl describe pod)",
    "What should I paste next?",
]
