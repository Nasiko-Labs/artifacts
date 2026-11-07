"""Pure: Alertmanager rules (AM001-AM005). AM004 also reads the pasted alert rules to find
warning/critical pairs of one alert name."""

from __future__ import annotations

from alerts import GRAFANA, PROMETHEUS, AmConfig, Parsed, Route
from findings import Finding, at_line, for_alert

PAGER_MIN_REPEAT_SECS = 3600
CRITICAL_MAX_REPEAT_SECS = 86400
MIN_GROUP_WAIT_SECS = 10
MIN_GROUP_INTERVAL_SECS = 60
_CRITICAL = frozenset({"critical", "page", "p1", "sev1"})


def check(parsed: Parsed) -> list[Finding]:
    found: list[Finding] = []
    for config in parsed.am:
        found += _grouping(parsed, config) + _receivers(parsed, config) + _timing(parsed, config)
        found += _double_paging(parsed, config) + _inhibit_pairs(parsed, config)
    return found


def _route_line(route: Route, key: str) -> int:
    return int(route.key_lines.get(key) or route.line)


def _grouping(parsed: Parsed, config: AmConfig) -> list[Finding]:
    found = []
    if config.root.group_by is None:
        found.append(at_line(parsed, "AM001", "medium", "route", config.file, _route_line(config.root, "receiver"),
                             "The root route has no `group_by`: one notification per alert."))
    for route in config.root.walk():
        if route.group_by and "..." in route.group_by and "group_by" in route.key_lines:
            found.append(at_line(parsed, "AM001", "medium", route.path, config.file, _route_line(route, "group_by"),
                                 "`group_by: ['...']` turns grouping off: one notification per alert."))
    return found


def _receivers(parsed: Parsed, config: AmConfig) -> list[Finding]:
    used = {r.receiver for r in config.root.walk()}
    found = []
    for receiver in config.receivers:
        if receiver.name not in used:
            found.append(at_line(parsed, "AM002", "medium", receiver.name, config.file, receiver.line,
                                 f"Receiver `{receiver.name}` is used by no route."))
    default = config.receiver(config.root.receiver)
    if default is not None and not default.kinds:
        found.append(at_line(parsed, "AM002", "high", default.name, config.file, default.line,
                             f"The default receiver `{default.name}` has no configs: unmatched alerts are dropped."))
    return found


def _is_pager(config: AmConfig, route: Route) -> bool:
    receiver = config.receiver(route.receiver)
    return receiver is not None and receiver.is_pager


def _timing(parsed: Parsed, config: AmConfig) -> list[Finding]:
    found = []
    for route in config.root.walk():
        repeat = route.repeat_interval_s if "repeat_interval" in route.key_lines else None
        line = _route_line(route, "repeat_interval")
        if repeat is not None and repeat < PAGER_MIN_REPEAT_SECS and _is_pager(config, route):
            found.append(at_line(parsed, "AM003", "medium", route.path, config.file, line,
                                 f"`repeat_interval` under 1h on the pager receiver `{route.receiver}`."))
        critical = any(m.name == "severity" and m.value.casefold() in _CRITICAL for m in route.matchers)
        if repeat is not None and repeat > CRITICAL_MAX_REPEAT_SECS and critical:
            found.append(at_line(parsed, "AM003", "medium", route.path, config.file, line,
                                 "`repeat_interval` over 24h on a critical route."))
        wait = route.group_wait_s if "group_wait" in route.key_lines else None
        interval = route.group_interval_s if "group_interval" in route.key_lines else None
        if wait is not None and wait < MIN_GROUP_WAIT_SECS:
            found.append(at_line(parsed, "AM005", "low", route.path, config.file, _route_line(route, "group_wait"),
                                 "`group_wait` under 10s."))
        if interval is not None and interval < MIN_GROUP_INTERVAL_SECS:
            found.append(at_line(parsed, "AM005", "low", route.path, config.file,
                                 _route_line(route, "group_interval"), "`group_interval` under 1m."))
    return found


def _double_paging(parsed: Parsed, config: AmConfig) -> list[Finding]:
    found = []
    for parent in config.root.walk():
        for i, route in enumerate(parent.children):
            later = parent.children[i + 1:]
            if route.keep_going and _is_pager(config, route) and any(_is_pager(config, r) for r in later):
                found.append(at_line(parsed, "AM005", "medium", route.path, config.file,
                                     _route_line(route, "continue"),
                                     f"`continue: true` on pager route `{route.receiver}` with a later pager route: "
                                     "double paging."))
    return found


def tier_pairs(parsed: Parsed) -> dict[str, dict[str, object]]:
    """Alert name -> {"warning": Alert, "critical": Alert} for names that have both."""
    pairs: dict[str, dict] = {}
    for alert in parsed.alerts:
        level = (alert.severity or "").casefold()
        if alert.vendor in (PROMETHEUS, GRAFANA) and level in ("warning", "critical"):
            pairs.setdefault(alert.name, {}).setdefault(level, alert)
    return {name: pair for name, pair in pairs.items() if len(pair) == 2}


def _inhibited(config: AmConfig, name: str) -> bool:
    source = {"alertname": name, "severity": "critical"}
    target = {"alertname": name, "severity": "warning"}
    return any(all(m.matches(source) for m in rule.source) and all(m.matches(target) for m in rule.target)
               and rule.source and rule.target for rule in config.inhibit)


def _inhibit_pairs(parsed: Parsed, config: AmConfig) -> list[Finding]:
    return [for_alert(parsed, "AM004", "medium", pair["warning"], "labels.severity",
                      f"`{name}` has warning and critical variants and no inhibit rule silences the warning.")
            for name, pair in tier_pairs(parsed).items() if not _inhibited(config, name)]
