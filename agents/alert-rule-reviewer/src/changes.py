"""Pure: proposed changes, built from code templates over the parsed alerts (never written by the model).
One change per (target, change type), carrying every finding id it answers. A change whose `after` holds a
number that is neither in the paste nor a template constant is dropped (and counted)."""

from __future__ import annotations

import re

import exprs
import snippets as sn
from alerts import CLOUDWATCH, DATADOG, Alert, Parsed
from changes_am import am_changes
from findings import Change, Finding
from rules_vendor import is_availability

FOR_CHOICES = (300, 600, 900)
EVALUATION_DELAY_SECS = 300
TEMPLATE_NUMBERS = frozenset({"5", "10", "15", "300"})
_NUMBER = re.compile(r"(?<![\w<])\d+(?:\.\d+)?")
_PLACEHOLDER = re.compile(r"<[^<>\n]*\?>")


def proposed_for(alert: Alert) -> int:
    """max(5m, 4 x the longest rate window), rounded up to 5m/10m/15m (capped at 15m)."""
    window = max((secs for _, secs, _ in exprs.range_windows(alert.expr)), default=0)
    wanted = max(FOR_CHOICES[0], 4 * window)
    return next((c for c in FOR_CHOICES if c >= wanted), FOR_CHOICES[-1])


def build(found: list[Finding], parsed: Parsed, owner_label: str | None) -> tuple[list[Change], int]:
    by_id = {a.id: a for a in parsed.alerts}
    planned: dict[tuple[str, str], tuple[list[str], Change]] = {}
    for finding in found:
        alert = by_id.get(finding.alert_id)
        change = _template(finding, alert, by_id, owner_label) if alert else None
        if change is None:
            continue
        ids, _first = planned.setdefault((change.target, change.change_type), ([], change))
        ids.append(finding.id)
    changes = [Change(c.target, tuple(ids), c.change_type, c.before, c.after, c.note) for ids, c in planned.values()]
    changes += am_changes(found, parsed)
    return _checked(changes, "\n".join(parsed.files.values()))


def _checked(changes: list[Change], paste: str) -> tuple[list[Change], int]:
    kept, dropped = [], 0
    for change in changes:
        numbers = set(_NUMBER.findall(_PLACEHOLDER.sub("", change.after)))
        if all(n in TEMPLATE_NUMBERS or n in paste for n in numbers):
            kept.append(change)
        else:
            dropped += 1
    return kept, dropped


def _template(f: Finding, a: Alert, by_id: dict, owner_label: str | None) -> Change | None:
    rule = f.rule_id
    if a.vendor in (DATADOG, CLOUDWATCH):
        return _vendor_template(f, a)
    if rule in ("AR001", "AR013") or (rule == "AR002" and "`for`" in f.message):
        new = exprs.fmt_duration(proposed_for(a))
        current = exprs.fmt_duration(a.for_s) if a.for_s is not None else None
        return _change(a, "add_for", sn.rule(a, expr=a.expr, **{"for": current}),
                       sn.rule(a, expr=a.expr, **{"for": new}),
                       "Pick from how long the condition must last before someone should act.")
    if rule == "AR004":
        key = owner_label or "team"
        return _change(a, "add_label", sn.rule(a, labels=dict(a.labels)),
                       sn.rule(a, labels={**a.labels, key: sn.OWNER_PLACEHOLDER}), "Fill in the owning team.")
    if rule == "AR003":
        return _change(a, "add_label", sn.rule(a, labels=dict(a.labels)),
                       sn.rule(a, labels={**a.labels, "severity": sn.SEVERITY_PLACEHOLDER}), "Choose one severity.")
    if rule == "AR005":
        extra = {"runbook_url": sn.RUNBOOK_PLACEHOLDER} if "runbook" in f.message else {
            "summary": sn.SUMMARY_PLACEHOLDER}
        return _change(a, "add_annotation", sn.rule(a, annotations=dict(a.annotations)),
                       sn.rule(a, annotations={**a.annotations, **extra}), "Fill in the placeholders.")
    if rule == "AR006" and f.severity == "high":
        return _change(a, "add_threshold", sn.rule(a, expr=a.expr),
                       sn.rule(a, expr=f"{a.expr} > {sn.THRESHOLD_PLACEHOLDER}"),
                       "No number is suggested: pick the level at which users are hurt.")
    if rule == "AR008":
        return _change(a, "downgrade_severity", sn.rule(a, labels=dict(a.labels)),
                       sn.rule(a, labels={**a.labels, "severity": "warning"}),
                       "Route it to a ticket; page on a symptom (an SLO burn alert) instead.")
    if rule == "AR007" and f.related in by_id:
        return _duplicate(f, a, by_id[f.related])
    return None


def _change(a: Alert, kind: str, before: dict, after: dict, note: str) -> Change:
    return Change(a.name, (), kind, sn.as_yaml(before), sn.as_yaml(after), note)


def _duplicate(f: Finding, a: Alert, first: Alert) -> Change:
    if not f.message.startswith("Overlapping"):
        return Change(a.name, (), "merge_duplicates", sn.as_yaml([sn.rule(first, expr=first.expr),
                                                                   sn.rule(a, expr=a.expr)]),
                      sn.as_yaml([sn.rule(first, expr=first.expr)]),
                      f"Keep `{first.name}` and delete `{a.name}`.")
    pair = sorted((first, a), key=lambda x: float((exprs.numbers_in(x.expr) or ["0"])[-1]))
    if re.search(r"<", exprs.normalise(a.expr)) and not re.search(r">", exprs.normalise(a.expr)):
        pair.reverse()  # "below N": the lower number is the worse one
    low, high = pair
    tiers = [sn.rule(low, expr=low.expr, **{"for": exprs.fmt_duration(proposed_for(low))},
                     labels={**low.labels, "severity": "warning"}),
             sn.rule(high, expr=high.expr, **{"for": exprs.fmt_duration(proposed_for(high))},
                     labels={**high.labels, "severity": "critical"})]
    return Change(a.name, (), "tier_severities", sn.as_yaml([sn.rule(first, expr=first.expr), sn.rule(a, expr=a.expr)]),
                  sn.as_yaml(tiers), "Warning at the milder threshold, critical at the worse one.")


def _vendor_template(f: Finding, a: Alert) -> Change | None:
    rule = f.rule_id
    if rule == "DD001":
        return Change(a.name, (), "add_notify_no_data", "", sn.vendor(a, {"notify_no_data": True}),
                      "Silence from an availability check should alert.")
    if rule == "DD005":
        return Change(a.name, (), "add_evaluation_delay", "", sn.vendor(a, {"evaluation_delay": EVALUATION_DELAY_SECS}),
                      "Cloud integration metrics arrive late; 300 s is the usual delay.")
    if rule in ("DD007", "CW005") and f.severity in ("medium", "low") and "env" not in f.message:
        tags = list(a.fields.get("tags") or [])
        if a.vendor == CLOUDWATCH and a.source != "hcl":
            new = [{"Key": "team", "Value": sn.OWNER_PLACEHOLDER}]
        elif a.vendor == CLOUDWATCH:
            return Change(a.name, (), "add_label", "", sn.vendor(a, {"tags": {"team": sn.OWNER_PLACEHOLDER}}),
                          "Fill in the owning team.")
        else:
            new = [*tags, f"team:{sn.OWNER_PLACEHOLDER}"]
        return Change(a.name, (), "add_label", "", sn.vendor(a, {"tags": new}), "Fill in the owning team.")
    if rule == "CW001":
        value = "breaching" if is_availability(a) else "notBreaching"
        return Change(a.name, (), "set_treat_missing_data", "", sn.vendor(a, {"treat_missing_data": value}),
                      "A choice: `breaching` treats silence as an outage (availability); `notBreaching` ignores gaps.")
    return None
