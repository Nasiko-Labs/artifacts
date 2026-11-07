"""Pure records: what the parsers produce and the rules read.

An `Alert` is one alerting rule, monitor or alarm, whichever vendor it came from. Alertmanager config is
kept as its own records (`AmConfig`: a route tree, receivers, inhibit rules). Every record knows the file
and line it came from, so a finding can cite `alerts.yml:12` and quote that line verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

PROMETHEUS, GRAFANA, DATADOG, CLOUDWATCH, ALERTMANAGER = "prometheus", "grafana", "datadog", "cloudwatch", "alertmanager"
FORMAT_NAMES = {PROMETHEUS: "Prometheus rules", GRAFANA: "Grafana-managed rules", DATADOG: "Datadog monitors",
                CLOUDWATCH: "CloudWatch alarms", ALERTMANAGER: "Alertmanager"}
OWNER_KEYS = ("team", "owner", "service", "squad")
PAGE_SEVERITIES = frozenset({"critical", "page", "p1", "p2", "sev1", "sev2", "emergency"})
PAGER_RECEIVER_KEYS = ("pagerduty_configs", "opsgenie_configs", "victorops_configs")
_PAGER_NAME = re.compile(r"pager|\bpage\b|on-?call|opsgenie|victorops", re.IGNORECASE)


@dataclass(frozen=True, eq=False)
class Alert:
    id: str                       # "a1", "a2", ... in paste order
    vendor: str
    name: str
    file: str
    line: int                     # the alert's first line in its file
    expr: str = ""                # PromQL / Datadog query / CloudWatch metric description
    for_s: int | None = None      # None: no `for` at all
    severity: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    annotations: Mapping[str, str] = field(default_factory=dict)
    group: str = ""
    fields: Mapping[str, Any] = field(default_factory=dict)   # vendor-specific fields (DD options, CW props)
    key_lines: Mapping[str, int] = field(default_factory=dict)  # field name -> line in file
    source: str = "yaml"          # yaml | json | hcl: how a proposed change is rendered

    def line_of(self, key: str) -> int:
        return int(self.key_lines.get(key) or self.line)


@dataclass(frozen=True)
class Matcher:
    name: str
    op: str                       # = != =~ !~
    value: str

    def matches(self, labels: Mapping[str, str]) -> bool:
        actual = str(labels.get(self.name, ""))
        if self.op in ("=~", "!~"):
            try:
                hit = re.fullmatch(self.value, actual) is not None
            except re.error:
                return False
            return hit if self.op == "=~" else not hit
        return (actual == self.value) if self.op == "=" else (actual != self.value)


@dataclass(frozen=True)
class Route:
    path: str                     # "route", "route.routes[0]", ...
    receiver: str
    matchers: tuple[Matcher, ...]
    line: int
    group_by: tuple[str, ...] | None = None
    group_wait_s: int | None = None
    group_interval_s: int | None = None
    repeat_interval_s: int | None = None
    keep_going: bool = False      # Alertmanager `continue`
    children: tuple[Route, ...] = ()
    key_lines: Mapping[str, int] = field(default_factory=dict)

    def matches(self, labels: Mapping[str, str]) -> bool:
        return all(m.matches(labels) for m in self.matchers)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class Receiver:
    name: str
    line: int
    kinds: tuple[str, ...]        # the *_configs keys it has

    @property
    def is_pager(self) -> bool:
        return any(k in PAGER_RECEIVER_KEYS for k in self.kinds) or bool(_PAGER_NAME.search(self.name))


@dataclass(frozen=True)
class InhibitRule:
    source: tuple[Matcher, ...]
    target: tuple[Matcher, ...]
    equal: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class AmConfig:
    file: str
    root: Route
    receivers: tuple[Receiver, ...]
    inhibit: tuple[InhibitRule, ...]
    line: int

    def receiver(self, name: str) -> Receiver | None:
        return next((r for r in self.receivers if r.name == name), None)

    def receivers_for(self, labels: Mapping[str, str]) -> list[str]:
        """The receivers an alert with these labels reaches (first match wins unless `continue`)."""
        return _route(self.root, labels) or [self.root.receiver]


def _route(node: Route, labels: Mapping[str, str]) -> list[str]:
    reached: list[str] = []
    for child in node.children:
        if not child.matches(labels):
            continue
        reached += _route(child, labels) or [child.receiver or node.receiver]
        if not child.keep_going:
            break
    return reached


@dataclass
class Parsed:
    """Everything parsed from one paste. Mutable while the parsers fill it, then read-only."""

    alerts: list[Alert] = field(default_factory=list)
    am: list[AmConfig] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # parse notes shown to the user
    skipped: list[str] = field(default_factory=list)    # blocks that could not be parsed
    files: dict[str, str] = field(default_factory=dict)  # file name -> its text (line numbers index this)

    def add_format(self, vendor: str) -> None:
        if vendor not in self.formats:
            self.formats.append(vendor)

    def next_id(self) -> str:
        return f"a{len(self.alerts) + 1}"

    @property
    def empty(self) -> bool:
        return not self.alerts and not self.am

    def line_text(self, file: str, line: int) -> str:
        lines = self.files.get(file, "").splitlines()
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def is_paging(alert: Alert, am: list[AmConfig]) -> bool:
    """Critical-like severity, a pager handle (Datadog), or a route to a pager receiver."""
    if (alert.severity or "").casefold() in PAGE_SEVERITIES:
        return True
    if alert.vendor == DATADOG and re.search(r"@(pagerduty|opsgenie|victorops)", str(alert.fields.get("message"))):
        return True
    labels = {**alert.labels, "alertname": alert.name}
    for config in am:
        for name in config.receivers_for(labels):
            receiver = config.receiver(name)
            if receiver is not None and receiver.is_pager:
                return True
    return False
