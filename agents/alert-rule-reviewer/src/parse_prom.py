"""Pure: Prometheus rule files (`groups[].rules[]`, also inside a PrometheusRule `spec`) and Grafana-managed
alert rules (provisioning YAML: rules with `uid`/`data`) into `Alert` records with line numbers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import exprs
from alerts import GRAFANA, PROMETHEUS, Alert, Parsed
from yamlload import LDict


def rule_groups(doc: Any) -> list | None:
    """The `groups` list when `doc` is a rule file (plain or PrometheusRule CRD), else None."""
    if not isinstance(doc, Mapping):
        return None
    spec = doc.get("spec")
    groups = doc.get("groups") if "groups" in doc else (spec.get("groups") if isinstance(spec, Mapping) else None)
    if not isinstance(groups, list) or not any(isinstance(g, Mapping) and "rules" in g for g in groups):
        return None
    return groups


def is_grafana_rule(rule: Mapping) -> bool:
    return "data" in rule or ("uid" in rule and "title" in rule)


def parse(doc: Any, file: str, parsed: Parsed) -> bool:
    groups = rule_groups(doc)
    if groups is None:
        return False
    recording = 0
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        for rule in group.get("rules") or ():
            if not isinstance(rule, Mapping):
                continue
            if "record" in rule and "alert" not in rule:
                recording += 1
                continue
            alert = _grafana(rule, group, file, parsed) if is_grafana_rule(rule) else _prometheus(rule, group, file,
                                                                                                    parsed)
            if alert is not None:
                parsed.alerts.append(alert)
                parsed.add_format(alert.vendor)
    if recording:
        parsed.notes.append(f"{recording} recording rule(s) in {file} skipped: they are not alerts.")
    return True


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in value.items()}


def _lines(rule: Any) -> dict[str, int]:
    lines = dict(getattr(rule, "key_lines", {}) or {})
    for key in ("labels", "annotations"):
        nested = rule.get(key)
        if isinstance(nested, LDict):
            lines.update({f"{key}.{k}": v for k, v in nested.key_lines.items()})
    return lines


def _prometheus(rule: Mapping, group: Mapping, file: str, parsed: Parsed) -> Alert | None:
    name = rule.get("alert")
    if not name:
        return None
    labels = _strings(rule.get("labels"))
    raw_for = rule.get("for")
    return Alert(id=parsed.next_id(), vendor=PROMETHEUS, name=str(name), file=file, line=getattr(rule, "line", 0),
                 expr=str(rule.get("expr") or "").strip(), for_s=None if raw_for is None else exprs.duration_secs(raw_for),
                 severity=labels.get("severity"), labels=labels, annotations=_strings(rule.get("annotations")),
                 group=str(group.get("name") or ""), key_lines=_lines(rule))


def _grafana_expr(rule: Mapping) -> str:
    exprs_found = []
    for query in rule.get("data") or ():
        model = query.get("model") if isinstance(query, Mapping) else None
        if isinstance(model, Mapping):
            text = model.get("expr") or model.get("expression") or model.get("query")
            if text:
                exprs_found.append(str(text).strip())
    return " ; ".join(exprs_found)


def _grafana(rule: Mapping, group: Mapping, file: str, parsed: Parsed) -> Alert:
    labels = _strings(rule.get("labels"))
    raw_for = rule.get("for")
    lines = _lines(rule)
    if "title" in lines:
        lines.setdefault("alert", lines["title"])
    return Alert(id=parsed.next_id(), vendor=GRAFANA, name=str(rule.get("title") or rule.get("uid") or "untitled"),
                 file=file, line=getattr(rule, "line", 0), expr=_grafana_expr(rule),
                 for_s=None if raw_for is None else exprs.duration_secs(raw_for), severity=labels.get("severity"),
                 labels=labels, annotations=_strings(rule.get("annotations")), group=str(group.get("name") or ""),
                 key_lines=lines)
