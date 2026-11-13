"""Pure: known inverses for state-changing commands (the rollback code supplies when the model gave none), and
the least-disruptive-first rank used to order mitigation steps."""

from __future__ import annotations

import re

NO_ROLLBACK = "No rollback: change is irreversible, get approval first"
ROLLBACK_UNKNOWN = "ROLLBACK_UNKNOWN"
ASK_OWNER = "Ask the service owner before running: "

_SCALE = re.compile(r"\bkubectl\b.*\bscale\b.*--replicas[= ]\s*\S+", re.IGNORECASE)
_REPLICAS = re.compile(r"--replicas[= ]\s*\S+", re.IGNORECASE)
_TARGET = re.compile(r"\b((?:deploy|deployment|statefulset|sts|rs|replicaset|daemonset|ds)/[\w.-]+)", re.IGNORECASE)
_NODE_OP = re.compile(r"\bkubectl\s+(?:cordon|drain)\s+(\S+)", re.IGNORECASE)
_HELM_UPGRADE = re.compile(r"\bhelm\s+upgrade\s+(?:--?\S+\s+)*([\w.-]+)", re.IGNORECASE)
_SYSTEMCTL = re.compile(r"\bsystemctl\s+(start|stop|restart|reload)\s+(\S+)", re.IGNORECASE)
_DOCKER = re.compile(r"\bdocker\s+(restart|stop|start)\s+(\S+)", re.IGNORECASE)
_REDIS_SET = re.compile(r"(\bredis-cli\b.*\bconfig\s+set\s+\S+)\s+\S+", re.IGNORECASE)
_FLAG = re.compile(r"\bflag\b|\bfeature\b|\btoggle\b", re.IGNORECASE)
_OFF = re.compile(r"\b(?:off|disable|disabled|false)\b", re.IGNORECASE)
_ON = re.compile(r"\b(?:on|enable|enabled|true)\b", re.IGNORECASE)
_CONFIG = re.compile(r"\bconfig(?:uration)?\b|\bset env\b|\benv var|\bsetting\b", re.IGNORECASE)

_RANKS = (
    (0, re.compile(r"\bscale\b|\brestart\b|\breload\b|\bbounce\b|\brecycle\b", re.IGNORECASE)),
    (1, re.compile(r"\bconfig\b|\bflag\b|\bset env\b|\btoggle\b|\bdisable\b|\benable\b|\blimit\b", re.IGNORECASE)),
    (2, re.compile(r"\brollback\b|\broll back\b|\brollout undo\b|\brevert\b|\bset image\b|\bredeploy\b",
                   re.IGNORECASE)),
    (3, re.compile(r"\bfail ?over\b|\bpromote\b|\bswitch traffic\b|\bdrain\b|\bcordon\b|\bevacuate\b",
                   re.IGNORECASE)),
)
OTHER_RANK = 4
DESTRUCTIVE_RANK = 5


def inverse(command: str, action: str = "") -> str | None:
    """The rollback for a state-changing step, or None when no inverse is known."""
    cmd = (command or "").strip()
    found = _command_inverse(cmd) if cmd else None
    return found or _action_inverse(f"{cmd} {action}")


def _command_inverse(cmd: str) -> str | None:
    lowered = cmd.casefold()
    if _SCALE.search(cmd):
        return f"Scale back to the previous count: `{_REPLICAS.sub('--replicas=<previous replicas>', cmd)}`"
    if "rollout restart" in lowered:
        return "No rollback needed: verify with `kubectl rollout status`"
    if "rollout undo" in lowered:
        return "Run `kubectl rollout undo` again to return to the revision before"
    if "set image" in lowered:
        target = _TARGET.search(cmd)
        return f"`kubectl rollout undo {target.group(1) if target else '<DEPLOYMENT>'}`"
    node = _NODE_OP.search(cmd)
    if node:
        return f"`kubectl uncordon {node.group(1)}`"
    helm = _HELM_UPGRADE.search(cmd)
    if helm:
        return f"`helm rollback {helm.group(1)} <previous revision>`"
    return _service_inverse(cmd)


def _service_inverse(cmd: str) -> str | None:
    for pattern, tool in ((_SYSTEMCTL, "systemctl"), (_DOCKER, "docker")):
        match = pattern.search(cmd)
        if not match:
            continue
        verb, unit = match.group(1).casefold(), match.group(2)
        if verb in ("restart", "reload"):
            check = f"systemctl status {unit}" if tool == "systemctl" else "docker ps"
            return f"No rollback needed: verify with `{check}`"
        opposite = "start" if verb == "stop" else "stop"
        return f"`{tool} {opposite} {unit}`"
    redis = _REDIS_SET.search(cmd)
    if redis:
        return f"Restore the previous value: `{redis.group(1)} <previous value>`"
    return None


def _action_inverse(text: str) -> str | None:
    if _FLAG.search(text):
        if _OFF.search(text):
            return "Turn the flag back on"
        if _ON.search(text):
            return "Turn the flag back off"
    if _CONFIG.search(text):
        return "Restore the previous value `<previous value>`"
    return None


def rank(text: str, destructive: bool = False) -> int:
    """Least disruptive first: scale/restart < config/flag < rollback < failover < anything else."""
    if destructive:
        return DESTRUCTIVE_RANK
    for value, pattern in _RANKS:
        if pattern.search(text or ""):
            return value
    return OTHER_RANK


_UNKNOWN = re.compile(r"r?oll?\s*back[\s_-]*unknown|^\W*(?:unknown|n/?a|none|tbd|null)\W*$", re.IGNORECASE)


def has_real_rollback(rollback: str | None) -> bool:
    """False for an empty rollback, the ROLLBACK_UNKNOWN sentinel (also as the model misspells it) or a bare
    "unknown"/"none"."""
    value = (rollback or "").strip()
    return bool(value) and not _UNKNOWN.search(value)
