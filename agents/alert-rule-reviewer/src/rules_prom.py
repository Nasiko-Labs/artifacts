"""Pure: the per-alert rules for Prometheus and Grafana-managed rules (AR001-AR006, AR009-AR013).
Grafana rules get the generic checks only (severity, owner, runbook); their expressions are not PromQL-checked.
AR008 needs the model's labels and lives in merge.py; cross-alert rules live in rules_cross.py."""

from __future__ import annotations

import re

import exprs
from alerts import GRAFANA, OWNER_KEYS, PROMETHEUS, Alert, Parsed
from detect import Settings
from findings import Finding, for_alert

FOR_MIN_UP_SECS = 120
WINDOW_SCRAPES = 4
FOR_SCRAPES = 2
_ABSENT_LABELS = re.compile(r"absent(?:_over_time)?\s*\(\s*[\w:]+\s*\{([^}]*)\}")


def check(parsed: Parsed, settings: Settings) -> list[Finding]:
    found: list[Finding] = []
    for alert in parsed.alerts:
        if alert.vendor in (PROMETHEUS, GRAFANA):
            found += _generic(parsed, alert, settings)
        if alert.vendor == PROMETHEUS:
            found += _promql(parsed, alert, settings)
    return found


def has_owner(alert: Alert, settings: Settings) -> bool:
    keys = (*OWNER_KEYS, *((settings.owner_label,) if settings.owner_label else ()))
    return any(alert.labels.get(k) for k in keys)


def _routed(alert: Alert, parsed: Parsed) -> bool:
    """A route other than the root's default receiver takes this alert."""
    labels = {**alert.labels, "alertname": alert.name}
    for config in parsed.am:
        if any(r is not config.root and r.matchers and r.matches(labels) and r.receiver != config.root.receiver
               for r in config.root.walk()):
            return True
    return False


def _generic(parsed: Parsed, alert: Alert, settings: Settings) -> list[Finding]:
    found = []
    if not alert.severity:
        found.append(for_alert(parsed, "AR003", "medium", alert, "labels", "No `severity` label."))
    if not has_owner(alert, settings) and not _routed(alert, parsed):
        where = " and no Alertmanager route other than the default takes it" if parsed.am else ""
        names = ", ".join(f"`{k}`" for k in (settings.owner_label or "team", "owner", "service"))
        found.append(for_alert(parsed, "AR004", "high", alert, "labels", f"No owner label ({names}){where}."))
    if not (alert.annotations.get("runbook_url") or alert.annotations.get("runbook")):
        found.append(for_alert(parsed, "AR005", "medium", alert, "annotations", "No `runbook_url` annotation."))
    if not (alert.annotations.get("summary") or alert.annotations.get("description")):
        found.append(for_alert(parsed, "AR005", "low", alert, "annotations", "No `summary` or `description`."))
    return found


def _promql(parsed: Parsed, alert: Alert, settings: Settings) -> list[Finding]:
    expr, found = alert.expr, []
    is_up = exprs.is_up_check(expr)
    no_for = not alert.for_s
    scrape = settings.scrape_s
    assumed = " (assumed 15s scrape interval)" if settings.scrape_assumed else ""
    if no_for and not is_up and not exprs.is_absence(expr) and (alert.severity or "").casefold() != "info":
        found.append(for_alert(parsed, "AR001", "medium", alert, "for" if alert.for_s == 0 else "alert",
                               "No `for` duration: one bad scrape fires it."))
    if alert.for_s and alert.for_s < FOR_SCRAPES * scrape and not is_up:
        found.append(for_alert(parsed, "AR002", "medium", alert, "for",
                               f"`for` is {exprs.fmt_duration(alert.for_s)}, under 2 scrapes{assumed}."))
    short = [raw for _, secs, raw in exprs.range_windows(expr) if secs < WINDOW_SCRAPES * scrape]
    if short:
        found.append(for_alert(parsed, "AR002", "medium", alert, "expr",
                               f"Range window `[{short[0]}]` is under 4 scrapes{assumed}."))
    found += _thresholds(parsed, alert)
    if exprs.uses_irate(expr):
        found.append(for_alert(parsed, "AR009", "medium", alert, "expr", "Uses `irate()`, which is spiky."))
    counters = exprs.bare_counters(expr)
    if counters and exprs.has_comparison(expr):
        found.append(for_alert(parsed, "AR010", "medium", alert, "expr",
                               f"`{counters[0]}` is compared without rate()/increase()."))
    if "absent(" in expr.replace(" ", "") and not is_up and _absent_has_labels(expr):
        found.append(for_alert(parsed, "AR011", "low", alert, "expr",
                               "`absent()` on a selector with labels the target may not always carry."))
    labels = exprs.by_labels(expr)
    if labels and exprs.high_cardinality(labels):
        found.append(for_alert(parsed, "AR012", "low", alert, "expr",
                               f"Groups by ({', '.join(labels)}): one alert per series."))
    if is_up and (alert.for_s or 0) < FOR_MIN_UP_SECS:
        current = exprs.fmt_duration(alert.for_s) if alert.for_s else "none"
        found.append(for_alert(parsed, "AR013", "medium", alert, "for" if alert.for_s else "expr",
                               f"Target-down check with `for` {current} (under 2m)."))
    return found


def _thresholds(parsed: Parsed, alert: Alert) -> list[Finding]:
    expr = alert.expr
    if expr and not exprs.has_comparison(expr) and not exprs.is_absence(expr):
        return [for_alert(parsed, "AR006", "high", alert, "expr",
                          "No comparison operator: it fires for every series the query returns.")]
    gauge = exprs.zero_gauge(expr)
    if gauge:
        return [for_alert(parsed, "AR006", "medium", alert, "expr",
                          f"Compares the gauge `{gauge}` against 0: any non-zero value fires.")]
    return []


def _absent_has_labels(expr: str) -> bool:
    for matcher_block in _ABSENT_LABELS.findall(expr):
        names = [m.split("=")[0].strip(" !~") for m in matcher_block.split(",") if m.strip()]
        if any(n and n != "job" for n in names):
            return True
    return False
