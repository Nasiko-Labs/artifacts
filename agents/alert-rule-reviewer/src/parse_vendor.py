"""Pure: Datadog monitors (API JSON/YAML and Terraform `datadog_monitor`) and CloudWatch alarms (Terraform
`aws_cloudwatch_metric_alarm` and CloudFormation `AWS::CloudWatch::Alarm`) into `Alert` records.

Each vendor's fields are flattened into `Alert.fields` under one spelling (Terraform's snake_case), so
the rules read one shape whatever the source was."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import hcl
from alerts import CLOUDWATCH, DATADOG, Alert, Parsed

DD_OPTION_KEYS = ("notify_no_data", "renotify_interval", "evaluation_delay", "require_full_window",
                  "escalation_message", "no_data_timeframe")
CFN_KEYS = {"AlarmName": "alarm_name", "TreatMissingData": "treat_missing_data",
            "EvaluationPeriods": "evaluation_periods", "DatapointsToAlarm": "datapoints_to_alarm", "Period": "period",
            "AlarmActions": "alarm_actions", "OKActions": "ok_actions", "Threshold": "threshold",
            "ThresholdMetricId": "threshold_metric_id", "MetricName": "metric_name", "Namespace": "namespace",
            "ComparisonOperator": "comparison_operator", "Metrics": "metric_query", "Tags": "tags"}
_CFN_ALARM = "AWS::CloudWatch::Alarm"


def datadog_monitors(doc: Any) -> list[Mapping] | None:
    if isinstance(doc, Mapping) and isinstance(doc.get("monitors"), list):
        doc = doc["monitors"]
    items = doc if isinstance(doc, list) else [doc]
    monitors = [m for m in items if isinstance(m, Mapping) and "type" in m and "query" in m]
    return monitors or None


def cfn_alarms(doc: Any) -> list[tuple[str, Mapping]] | None:
    if not isinstance(doc, Mapping):
        return None
    if doc.get("Type") == _CFN_ALARM:
        return [("Alarm", doc)]
    resources = doc.get("Resources")
    if not isinstance(resources, Mapping):
        return None
    alarms = [(str(k), v) for k, v in resources.items() if isinstance(v, Mapping) and v.get("Type") == _CFN_ALARM]
    return alarms or None


def _tags(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [f"{k}:{v}" for k, v in value.items()]
    if isinstance(value, list):
        out = []
        for tag in value:
            if isinstance(tag, Mapping) and "Key" in tag:
                out.append(f"{tag['Key']}:{tag.get('Value', '')}")
            else:
                out.append(str(tag))
        return out
    return []


def _dd_alert(monitor: Mapping, file: str, line: int, lines: dict, parsed: Parsed, source: str) -> Alert:
    options = monitor.get("options") if isinstance(monitor.get("options"), Mapping) else {}
    thresholds = options.get("thresholds") or monitor.get("monitor_thresholds") or {}
    fields = {"type": str(monitor.get("type") or ""), "query": str(monitor.get("query") or ""),
              "message": str(monitor.get("message") or ""), "tags": _tags(monitor.get("tags")),
              "priority": monitor.get("priority"), "critical": None, "warning": None,
              "resource": monitor.get("resource")}
    if isinstance(thresholds, Mapping):
        fields["critical"], fields["warning"] = thresholds.get("critical"), thresholds.get("warning")
    for key in DD_OPTION_KEYS:
        fields[key] = options.get(key, monitor.get(key))
    return Alert(id=parsed.next_id(), vendor=DATADOG, name=str(monitor.get("name") or "unnamed monitor"), file=file,
                 line=line, expr=fields["query"], fields=fields, key_lines=lines, source=source)


def _nested_lines(node: Any, prefix_keys: tuple[str, ...] = ("options", "thresholds")) -> dict[str, int]:
    lines = dict(getattr(node, "key_lines", {}) or {})
    for key in prefix_keys:
        child = node.get(key) if isinstance(node, Mapping) else None
        if child is not None:
            lines.update({k: v for k, v in _nested_lines(child, prefix_keys).items() if k not in lines})
    return lines


def parse_datadog(doc: Any, file: str, parsed: Parsed, source: str) -> bool:
    monitors = datadog_monitors(doc)
    if monitors is None:
        return False
    for monitor in monitors:
        parsed.alerts.append(_dd_alert(monitor, file, getattr(monitor, "line", 0), _nested_lines(monitor), parsed,
                                       source))
    parsed.add_format(DATADOG)
    return True


def _cw_alert(fields: dict, name: str, file: str, line: int, lines: dict, parsed: Parsed, source: str) -> Alert:
    fields = {**fields, "tags": _tags(fields.get("tags"))}
    expr = " ".join(str(fields.get(k)) for k in ("namespace", "metric_name", "comparison_operator", "threshold")
                    if fields.get(k) is not None)
    return Alert(id=parsed.next_id(), vendor=CLOUDWATCH, name=str(fields.get("alarm_name") or name), file=file,
                 line=line, expr=expr, fields=fields, key_lines=lines, source=source)


def parse_cfn(doc: Any, file: str, parsed: Parsed) -> bool:
    alarms = cfn_alarms(doc)
    if alarms is None:
        return False
    for logical_id, resource in alarms:
        props = resource.get("Properties") if isinstance(resource.get("Properties"), Mapping) else {}
        fields = {CFN_KEYS[k]: v for k, v in props.items() if k in CFN_KEYS}
        raw_lines = getattr(props, "key_lines", {}) or {}
        lines = {CFN_KEYS.get(k, k): v for k, v in raw_lines.items()}
        line = getattr(resource, "line", 0) or getattr(props, "line", 0)
        parsed.alerts.append(_cw_alert(fields, logical_id, file, line, lines, parsed, "yaml"))
    parsed.add_format(CLOUDWATCH)
    return True


def parse_hcl(text: str, file: str, parsed: Parsed) -> None:
    found, notes = hcl.resources(text)
    parsed.skipped.extend(f"{file}: {note}" for note in notes)
    for resource in found:
        if resource.type == "datadog_monitor":
            attrs = {**resource.attrs, "resource": resource.name}
            parsed.alerts.append(_dd_alert(attrs, file, resource.line, resource.key_lines, parsed, "hcl"))
            parsed.add_format(DATADOG)
        else:
            parsed.alerts.append(_cw_alert({**resource.attrs, "resource": resource.name}, resource.name, file, resource.line,
                                           resource.key_lines, parsed, "hcl"))
            parsed.add_format(CLOUDWATCH)
