"""Pure: render a proposed change's before/after as the source's own syntax (YAML, JSON or HCL), with only
the keys that change. Placeholders like `<owner?>` stay visible; nothing is guessed."""

from __future__ import annotations

import json
from typing import Any

import yaml

from alerts import CLOUDWATCH, DATADOG, Alert

OWNER_PLACEHOLDER = "<owner?>"
RUNBOOK_PLACEHOLDER = "<runbook-url?>"
SUMMARY_PLACEHOLDER = "<one-line summary?>"
THRESHOLD_PLACEHOLDER = "<threshold?>"
SEVERITY_PLACEHOLDER = "<warning|critical?>"


def as_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120).rstrip()


def as_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _hcl_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_hcl_value(v) for v in value) + "]"
    return json.dumps(str(value))


def as_hcl(kind: str, name: str, attrs: dict) -> str:
    body = "\n".join(f"  {k} = {_hcl_value(v)}" for k, v in attrs.items())
    return f'resource "{kind}" "{name}" {{\n{body}\n}}'


def rule(alert: Alert, **keys: Any) -> dict:
    """A Prometheus/Grafana rule with only the keys given (None values dropped)."""
    head = {"alert" if alert.vendor != "grafana" else "title": alert.name}
    return {**head, **{k: v for k, v in keys.items() if v is not None}}


def vendor(alert: Alert, attrs: dict) -> str:
    """Datadog/CloudWatch attributes in the alert's own source syntax."""
    if alert.source == "hcl":
        kind = "datadog_monitor" if alert.vendor == DATADOG else "aws_cloudwatch_metric_alarm"
        return as_hcl(kind, _resource_name(alert), attrs)
    if alert.vendor == CLOUDWATCH:
        return as_yaml({"Properties": {_cfn_key(k): v for k, v in attrs.items()}})
    options = {k: v for k, v in attrs.items() if k not in ("tags", "message", "query")}
    top = {k: v for k, v in attrs.items() if k in ("tags", "message", "query")}
    data = {"name": alert.name, **top, **({"options": options} if options else {})}
    return as_json(data) if alert.source == "json" else as_yaml(data)


def _resource_name(alert: Alert) -> str:
    if alert.fields.get("resource"):
        return str(alert.fields["resource"])
    return "".join(c if c.isalnum() or c == "_" else "_" for c in alert.name.lower()).strip("_") or "monitor"


_CFN = {"treat_missing_data": "TreatMissingData", "tags": "Tags", "alarm_actions": "AlarmActions",
        "evaluation_periods": "EvaluationPeriods", "datapoints_to_alarm": "DatapointsToAlarm"}


def _cfn_key(key: str) -> str:
    return _CFN.get(key, key)
