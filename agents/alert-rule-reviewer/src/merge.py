"""Pure: AR008 (cause-based paging), the one rule that needs the model's labels. It fires when an alert pages
(critical-like severity, a pager handle, or a route to a pager receiver) and its label is `cause`. The
message is code-written; the model's own words are never shown."""

from __future__ import annotations

from alerts import DATADOG, Alert, Parsed, is_paging
from findings import Finding, for_alert
from label import Labels

EXPR_SHOWN_CHARS = 80


def _key(alert: Alert) -> str:
    if "labels.severity" in alert.key_lines:
        return "labels.severity"
    return "message" if alert.vendor == DATADOG and "message" in alert.key_lines else "alert"


def cause_paging(parsed: Parsed, labels: Labels) -> list[Finding]:
    found = []
    for alert in parsed.alerts:
        if labels.kinds.get(alert.id) != "cause" or not is_paging(alert, parsed.am):
            continue
        how = f"severity `{alert.severity}`" if alert.severity else "a pager"
        condition = alert.expr[:EXPR_SHOWN_CHARS] or alert.name
        found.append(for_alert(parsed, "AR008", "high", alert, _key(alert),
                               f"Pages via {how} on a cause signal, not a user symptom. Ask: is a user hurt when "
                               f"`{condition}` is true?"))
    return found
