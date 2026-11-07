"""Pure: Datadog monitor rules (DD001-DD009) and CloudWatch alarm rules (CW001-CW005)."""

from __future__ import annotations

import re

import exprs
from alerts import CLOUDWATCH, DATADOG, Alert, Parsed
from findings import Finding, for_alert

_AVAILABILITY = re.compile(r"\bup\b|health|heartbeat|availab|\.up\b|ping|uptime", re.IGNORECASE)
_PAGER_HANDLE = re.compile(r"@(?:pagerduty|opsgenie|victorops)[\w.-]*", re.IGNORECASE)
_HANDLE = re.compile(r"(?<![\w.])@([\w.-]+)")
_CLOUD_METRIC = re.compile(r"(?:^|[:(\s,])(aws|gcp|azure)\.", re.IGNORECASE)
_SHORT_WINDOW = re.compile(r"\blast_(1|5)m\b")
_SPARSE = re.compile(r"\.as_count\(\)|\berrors?\b|\.count\b|\bhits\b", re.IGNORECASE)
_OWNER_TAG = re.compile(r"^(team|owner|service|squad):", re.IGNORECASE)
_ANOMALY = re.compile(r"ANOMALY_DETECTION_BAND", re.IGNORECASE)
ONE_MINUTE_SECS = 60


def check(parsed: Parsed) -> list[Finding]:
    found: list[Finding] = []
    for alert in parsed.alerts:
        if alert.vendor == DATADOG:
            found += _datadog(parsed, alert)
        elif alert.vendor == CLOUDWATCH:
            found += _cloudwatch(parsed, alert)
    return found + _dd_duplicates(parsed)


def is_availability(alert: Alert) -> bool:
    return bool(_AVAILABILITY.search(alert.name) or _AVAILABILITY.search(alert.expr))


def _unset(value) -> bool:
    return value is None or value == "" or value is False or value == 0


def _datadog(parsed: Parsed, alert: Alert) -> list[Finding]:
    f, found = alert.fields, []
    message, pages = str(f.get("message") or ""), bool(_PAGER_HANDLE.search(str(f.get("message") or "")))
    if is_availability(alert) and not f.get("notify_no_data"):
        found.append(for_alert(parsed, "DD001", "high", alert, "notify_no_data" if "notify_no_data" in alert.key_lines
                               else "name", "Availability monitor without `notify_no_data`: silence looks healthy."))
    if f.get("critical") is None and not exprs.has_comparison(alert.expr):
        found.append(for_alert(parsed, "DD002", "high", alert, "query", "No critical threshold."))
    elif f.get("warning") is None:
        found.append(for_alert(parsed, "DD002", "medium", alert, "query", "No warning threshold."))
    if pages and _unset(f.get("renotify_interval")):
        found.append(for_alert(parsed, "DD003", "medium", alert, "message", "Pages but never renotifies."))
    handles = {h.casefold() for h in _HANDLE.findall(message)}
    if not handles or handles <= {"all", "here", "channel"}:
        found.append(for_alert(parsed, "DD004", "high", alert, "message" if "message" in alert.key_lines else "name",
                               "The message has no @handle: nobody is notified." if not handles
                               else "Only @all/@here: no one specific is notified."))
    if _CLOUD_METRIC.search(alert.expr) and _unset(f.get("evaluation_delay")):
        found.append(for_alert(parsed, "DD005", "medium", alert, "query", "Cloud metric with no `evaluation_delay`."))
    full_window = f.get("require_full_window")
    if _SHORT_WINDOW.search(alert.expr) and full_window is not False and _SPARSE.search(alert.expr):
        found.append(for_alert(parsed, "DD006", "medium", alert, "query",
                               "Short window with a full window required on a sparse (count) metric."))
    tags = [str(t) for t in f.get("tags") or ()]
    if not any(_OWNER_TAG.match(t) for t in tags):
        found.append(for_alert(parsed, "DD007", "medium", alert, "tags" if "tags" in alert.key_lines else "name",
                               "No `team:`/`owner:`/`service:` tag."))
    if not any(t.casefold().startswith("env:") for t in tags):
        found.append(for_alert(parsed, "DD007", "low", alert, "tags" if "tags" in alert.key_lines else "name",
                               "No `env:` tag."))
    if f.get("priority") in (None, ""):
        found.append(for_alert(parsed, "DD009", "low", alert, "name", "No `priority`."))
    if pages and not f.get("escalation_message"):
        found.append(for_alert(parsed, "DD009", "low", alert, "message", "Pages with no `escalation_message`."))
    return found


def _dd_duplicates(parsed: Parsed) -> list[Finding]:
    seen: dict[str, Alert] = {}
    found = []
    for alert in (a for a in parsed.alerts if a.vendor == DATADOG and a.expr):
        key = exprs.normalise(alert.expr)
        first = seen.setdefault(key, alert)
        if first is not alert:
            found.append(for_alert(parsed, "DD008", "medium", alert, "query",
                                   f"Same query as `{first.name}` ({first.file}:{first.line})."))
    return found


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _cloudwatch(parsed: Parsed, alert: Alert) -> list[Finding]:
    f, found = alert.fields, []
    name_key = "alarm_name" if "alarm_name" in alert.key_lines else "name"
    if not f.get("treat_missing_data"):
        found.append(for_alert(parsed, "CW001", "medium", alert, name_key,
                               "`treat_missing_data` unset (defaults to `missing`)."))
    period = _int(f.get("period"))
    if _int(f.get("evaluation_periods")) == 1 and not f.get("datapoints_to_alarm") and period == ONE_MINUTE_SECS:
        found.append(for_alert(parsed, "CW002", "medium", alert, "evaluation_periods",
                               "One 1-minute evaluation period: a single sample fires it."))
    if not f.get("alarm_actions"):
        found.append(for_alert(parsed, "CW003", "high", alert, name_key, "No `alarm_actions`: nobody is notified."))
    if not f.get("ok_actions"):
        found.append(for_alert(parsed, "CW003", "low", alert, name_key, "No `ok_actions`."))
    metric_query = str(f.get("metric_query") or "")
    if f.get("threshold") is None and not f.get("threshold_metric_id") and not _ANOMALY.search(metric_query):
        found.append(for_alert(parsed, "CW004", "medium", alert, name_key, "No `threshold` and no anomaly band."))
    tags = [str(t) for t in f.get("tags") or ()]
    if not any(_OWNER_TAG.match(t) for t in tags):
        found.append(for_alert(parsed, "CW005", "low", alert, "tags" if "tags" in alert.key_lines else name_key,
                               "No team/owner tag."))
    return found
