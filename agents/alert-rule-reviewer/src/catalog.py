"""The rule catalog: a title, why it matters and a fix hint per rule id. "Why is AR004 a problem" is answered
from here with no model call. Wording is this agent's own."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    title: str
    why: str
    fix: str


RULES = {
    "AR001": Rule("no `for` duration", "Without `for`, one bad scrape or a single spike fires the alert at once, so "
                  "it flaps.", "Add `for:` so the condition must hold for a few minutes first."),
    "AR002": Rule("window too short for the scrape interval", "A `for` under two scrapes or a rate window under four "
                  "scrapes is decided by one or two samples, so it flaps.", "Lengthen `for` or the rate window."),
    "AR003": Rule("no severity label", "Routing and inhibition usually key on `severity`; without it the alert takes "
                  "the default path.", "Add `severity: warning` or `critical`."),
    "AR004": Rule("no owner", "No team/owner/service label and no specific route, so it lands with the default "
                  "receiver and nobody in particular owns it.", "Add an owner label (e.g. `team`) or a route."),
    "AR005": Rule("no runbook or summary", "Whoever is woken has no runbook link or summary to act on.",
                  "Add `runbook_url` and `summary` annotations."),
    "AR006": Rule("missing threshold", "With no comparison the alert fires for every series the query returns.",
                  "Add a comparison (`> <threshold>`) or use `absent()` for missing data."),
    "AR007": Rule("duplicate or overlapping alerts", "Two rules with the same expression page twice for one "
                  "problem; overlapping thresholds should be severity tiers.", "Merge them, or tier severities."),
    "AR008": Rule("cause-based paging", "Paging on a resource indicator (CPU, memory, disk, queue) wakes people when "
                  "users may be fine; page on symptoms users feel.", "Downgrade to a ticket or page on an SLO burn."),
    "AR009": Rule("irate() in an alert", "irate() looks at the last two samples only, so the value is spiky.",
                  "Use rate() over a window of at least 4 scrapes."),
    "AR010": Rule("bare counter compared", "A `_total` counter only grows, so once past the threshold it fires "
                  "until the process restarts.", "Compare rate() or increase() of it instead."),
    "AR011": Rule("absent() with label selectors", "absent() with labels fires whenever that label set disappears, "
                  "including relabels and renames.", "Check absent() on the job, or on labels the target always has."),
    "AR012": Rule("high-cardinality grouping", "Grouping by per-instance or per-request labels makes one alert per "
                  "series: an alert storm.", "Aggregate by service or cluster."),
    "AR013": Rule("target-down alert too quick", "An `up == 0` check under 2m fires on restarts and deploys.",
                  "Use `for: 2m` or more."),
    "AR014": Rule("inconsistent alert name", "Most names here follow one style; this one does not, which makes "
                  "routing and search harder.", "Rename it in the common style."),
    "AR015": Rule("inconsistent severity values", "Mixed spellings (`critical`, `Critical`, `sev1`) mean routes "
                  "that match one miss the others.", "Use one set of severity values."),
    "AM001": Rule("no grouping", "Without `group_by` (or with `'...'`) every alert is its own notification: an "
                  "alert storm.", "Group by alertname, cluster and service."),
    "AM002": Rule("orphan or empty receiver", "A receiver no route uses is dead config; a default receiver with "
                  "no configs drops alerts silently.", "Remove it, route to it, or give it a config."),
    "AM003": Rule("repeat interval", "Under 1h on a pager repeats pages; over 24h on a critical route hides a "
                  "still-firing problem.", "Use 1h-12h on pager routes."),
    "AM004": Rule("no inhibit rule for tiers", "Warning and critical variants both notify when the critical one "
                  "fires.", "Inhibit the warning while the critical fires."),
    "AM005": Rule("timing or double paging", "Very short group_wait/group_interval floods; `continue: true` on a "
                  "pager route with a later pager route pages twice.", "Raise the timers; drop `continue`."),
    "DD001": Rule("no-data not notified", "An availability monitor without notify_no_data stays quiet when the "
                  "thing stops reporting at all.", "Set notify_no_data: true."),
    "DD002": Rule("thresholds", "With no critical threshold the monitor cannot alert; without a warning there is "
                  "no early signal.", "Set thresholds.critical and warning."),
    "DD003": Rule("no renotify on a paging monitor", "A page that is missed is never repeated.",
                  "Set renotify_interval."),
    "DD004": Rule("nobody notified", "The message has no @handle (or only @all/@here), so no one specific is "
                  "told.", "Add a team or pager @handle."),
    "DD005": Rule("no evaluation delay on a cloud metric", "Cloud integration metrics arrive late; without a delay "
                  "the monitor evaluates gaps.", "Set evaluation_delay (e.g. 300 s)."),
    "DD006": Rule("short full window on a sparse metric", "A 1-5 minute window that needs a full window of a "
                  "sparse metric flaps or never evaluates.", "Widen the window or set require_full_window false."),
    "DD007": Rule("no owner or env tag", "Without team/owner/service tags nobody owns it; without env you cannot "
                  "filter.", "Add `team:<owner?>` and `env:` tags."),
    "DD008": Rule("duplicate monitor", "Two monitors with the same query notify twice.", "Keep one."),
    "DD009": Rule("no priority or escalation", "Without priority triage is guesswork; a paging monitor without an "
                  "escalation message never escalates.", "Set priority and escalation_message."),
    "CW001": Rule("treat_missing_data unset", "The default (`missing`) can leave an outage in INSUFFICIENT_DATA "
                  "instead of ALARM.", "Set treat_missing_data explicitly."),
    "CW002": Rule("single-sample alarm", "One 1-minute datapoint flips the alarm.",
                  "Use 3+ evaluation periods or datapoints_to_alarm."),
    "CW003": Rule("no actions", "No alarm_actions means nobody is notified; no ok_actions means no all-clear.",
                  "Add an SNS topic to alarm_actions."),
    "CW004": Rule("missing threshold", "No threshold and no anomaly band: the alarm cannot evaluate.",
                  "Set threshold or an anomaly detection band."),
    "CW005": Rule("no owner tag", "Nobody owns the alarm.", "Add a team/owner tag."),
}
RULE_ID = re.compile(r"\b(AR0\d\d|AM0\d\d|DD0\d\d|CW0\d\d)\b", re.IGNORECASE)


def explain(rule_id: str) -> str | None:
    rule = RULES.get(rule_id.upper())
    if rule is None:
        return None
    return f"**{rule_id.upper()}: {rule.title}.** {rule.why}\n\nFix: {rule.fix}"
