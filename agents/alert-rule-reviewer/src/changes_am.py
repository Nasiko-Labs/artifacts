"""Pure: proposed Alertmanager changes (AM001 add_group_by, AM004 add_inhibit_rule), from code templates."""

from __future__ import annotations

import snippets as sn
from alerts import Parsed
from findings import Change, Finding
from rules_am import tier_pairs

GROUP_BY_CANDIDATES = ("cluster", "service")


def group_by_labels(parsed: Parsed) -> list[str]:
    """`alertname` plus `cluster`/`service` when the pasted rules carry them."""
    present = {k for a in parsed.alerts for k in a.labels}
    return ["alertname", *(label for label in GROUP_BY_CANDIDATES if label in present)]


def am_changes(found: list[Finding], parsed: Parsed) -> list[Change]:
    changes = []
    grouping = [f for f in found if f.rule_id == "AM001"]
    if grouping and parsed.am:
        root = parsed.am[0].root
        changes.append(Change("route", tuple(f.id for f in grouping), "add_group_by",
                              sn.as_yaml({"route": {"receiver": root.receiver}}),
                              sn.as_yaml({"route": {"receiver": root.receiver, "group_by": group_by_labels(parsed)}}),
                              "One notification per alert group instead of per alert."))
    inhibit = [f for f in found if f.rule_id == "AM004"]
    if inhibit:
        names = sorted(n for n in tier_pairs(parsed) if any(f.target == n for f in inhibit))
        rule = {"source_matchers": ['severity="critical"', f'alertname=~"{"|".join(names)}"'],
                "target_matchers": ['severity="warning"', f'alertname=~"{"|".join(names)}"'],
                "equal": ["alertname"]}
        changes.append(Change("inhibit_rules", tuple(f.id for f in inhibit), "add_inhibit_rule", "",
                              sn.as_yaml({"inhibit_rules": [rule]}),
                              "Silences each warning while its critical variant fires."))
    return changes
