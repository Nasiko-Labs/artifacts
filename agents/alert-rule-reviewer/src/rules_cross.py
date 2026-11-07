"""Pure: rules over the whole set of Prometheus/Grafana alerts: duplicates and overlapping thresholds
(AR007), name style (AR014) and severity spellings (AR015)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

import exprs
from alerts import GRAFANA, PROMETHEUS, Alert, Parsed
from findings import Finding, for_alert

STYLE_SHARE = 0.8
MIN_NAMES_FOR_STYLE = 3
_STYLES = (("CamelCase", re.compile(r"^[A-Z][A-Za-z0-9]*$")), ("snake_case", re.compile(r"^[a-z][a-z0-9_]*$")),
           ("kebab-case", re.compile(r"^[a-z][a-z0-9-]*$")), ("UPPER_CASE", re.compile(r"^[A-Z][A-Z0-9_]*$")))
_VOCAB = {"critical": "words", "warning": "words", "info": "words", "page": "words", "ticket": "words",
          "none": "words", "error": "words", "high": "levels", "medium": "levels", "low": "levels"}
_NUMBERED = re.compile(r"^(sev|p)\s*-?\d$", re.IGNORECASE)


def _rule_alerts(parsed: Parsed) -> list[Alert]:
    return [a for a in parsed.alerts if a.vendor in (PROMETHEUS, GRAFANA)]


def check(parsed: Parsed) -> list[Finding]:
    alerts = _rule_alerts(parsed)
    return [*_duplicates(parsed, alerts), *_names(parsed, alerts), *_severities(parsed, alerts)]


def _labels_without(alert: Alert, *drop: str) -> tuple:
    return tuple(sorted((k, v) for k, v in alert.labels.items() if k not in drop))


def _duplicates(parsed: Parsed, alerts: list[Alert]) -> list[Finding]:
    found: list[Finding] = []
    exact: dict[str, Alert] = {}
    shape: dict[tuple, Alert] = {}
    for alert in alerts:
        if not alert.expr:
            continue
        key = exprs.normalise(alert.expr)
        first = exact.get(key)
        if first is not None:
            found.append(replace(for_alert(parsed, "AR007", "medium", alert, "expr",
                                           f"Same expression as `{first.name}` ({first.file}:{first.line}): a "
                                           "duplicate."), related=first.id))
            continue
        exact[key] = alert
        loose = (exprs.normalise(alert.expr, numbers=False), _labels_without(alert))
        other = shape.get(loose)
        if other is not None:
            found.append(replace(for_alert(parsed, "AR007", "medium", alert, "expr",
                                           f"Overlapping thresholds with `{other.name}` ({other.file}:{other.line}) "
                                           "at the same severity: candidates for severity tiers."),
                                 related=other.id))
        else:
            shape[loose] = alert
    found += _same_names(parsed, alerts)
    return found


def _same_names(parsed: Parsed, alerts: list[Alert]) -> list[Finding]:
    """Two rules with one name in one group and the same severity (a warning/critical pair is a tier)."""
    seen: dict[tuple, Alert] = {}
    found = []
    for alert in alerts:
        key = (alert.file, alert.group, alert.name, (alert.severity or "").casefold())
        first = seen.setdefault(key, alert)
        if first is not alert and exprs.normalise(first.expr) != exprs.normalise(alert.expr):
            found.append(for_alert(parsed, "AR007", "medium", alert, "alert",
                                   f"Second rule named `{alert.name}` in group `{alert.group}` with the same "
                                   "severity."))
    return found


def _style(name: str) -> str:
    return next((label for label, pattern in _STYLES if pattern.match(name)), "other")


def _names(parsed: Parsed, alerts: list[Alert]) -> list[Finding]:
    if len(alerts) < MIN_NAMES_FOR_STYLE:
        return []
    styles = Counter(_style(a.name) for a in alerts)
    common, count = styles.most_common(1)[0]
    if common == "other" or count / len(alerts) < STYLE_SHARE or count == len(alerts):
        return []
    return [for_alert(parsed, "AR014", "low", a, "alert", f"Name is not {common} like {count} of the "
                      f"{len(alerts)} others.") for a in alerts if _style(a.name) != common]


def _family(value: str) -> str:
    if _NUMBERED.match(value):
        return "numbered"
    return _VOCAB.get(value.casefold(), "other")


def _severities(parsed: Parsed, alerts: list[Alert]) -> list[Finding]:
    rated = [a for a in alerts if a.severity]
    values = Counter(a.severity for a in rated)
    if len(values) < 2:
        return []
    families = Counter(_family(a.severity) for a in rated)
    main_family = families.most_common(1)[0][0]
    spelling: dict[str, str] = {}
    for value, _ in values.most_common():
        spelling.setdefault(value.casefold(), value)
    found = []
    for alert in rated:
        odd_family = _family(alert.severity) != main_family
        odd_case = spelling[alert.severity.casefold()] != alert.severity
        if odd_family or odd_case:
            why = (f"`{alert.severity}` mixes with `{spelling[alert.severity.casefold()]}`" if odd_case
                   else f"`{alert.severity}` is not in the set most alerts use")
            found.append(for_alert(parsed, "AR015", "medium", alert, "labels.severity",
                                   f"Severity {why}: routes matching one spelling miss it."))
    return found
