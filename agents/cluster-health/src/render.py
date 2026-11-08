"""Pure: the markdown reply and the JSON envelope, both rendered in code. No raw HTML, no images, no links;
evidence lines go in code spans with their padding shortened."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import analysis
import group

_PADDING = re.compile(r" {3,}")
SHOWN_EVIDENCE = 3
SEVERITY_LABEL = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
ISSUE_FIELDS = ("id", "kind", "severity", "namespace", "object", "object_type", "count", "examples", "evidence",
                "detail", "explanation", "likely_causes", "next_command", "flags", "correlation")


def code(text: str) -> str:
    return "`" + _PADDING.sub("  ", text.replace("`", "'").strip()) + "`"


def headline(result: dict) -> str:
    counts = result["counts"]
    parts = ["**Cluster health**", result.get("label") or "", "as of your paste", group.pods_phrase(counts),
             group.nodes_phrase(counts)]
    return " · ".join(p for p in parts if p)


def summary_text(result: dict) -> str:
    """The one-line summary (envelope `summary`), counts from code."""
    by = result["counts"]["issues_by_severity"]
    found = ", ".join(f"{n} {s}" for s, n in by.items() if n)
    pods, nodes = group.pods_phrase(result["counts"]), group.nodes_phrase(result["counts"])
    scope = "; ".join(p for p in (pods, nodes) if p)
    return (f"Issues: {found}." if found else "No unhealthy objects found.") + (f" {scope}." if scope else "")


def issue_title(issue: dict) -> str:
    where = f"{issue['namespace']}/{issue['object']}" if issue["namespace"] else issue["object"]
    if issue["object_type"] == "node":
        where = f"node {issue['object']}"
    many = f" ({issue['count']} pods)" if issue["object_type"] == "workload" and issue["count"] > 1 else ""
    return f"**{SEVERITY_LABEL[issue['severity']]}** {where}{many}: {issue['detail']}"


def render_issue(issue: dict, n: int, full: bool = False) -> str:
    lines = [f"{n}. {issue_title(issue)}"]
    evidence = issue["evidence"] if full else issue["evidence"][:SHOWN_EVIDENCE]
    lines += [f"   - {code(line)}" for line in evidence]
    if issue["count"] > 1 and issue["object_type"] == "workload":
        lines.append(f"   - Pods: {', '.join(issue['examples'])}" + (" …" if issue["count"] > len(issue["examples"]) else ""))
    if issue.get("correlation"):
        lines.append(f"   - Rollout: {issue['correlation']}")
    if issue.get("explanation"):
        lines.append(f"   - Why: {issue['explanation']}")
    if issue.get("likely_causes"):
        lines.append(f"   - Likely: {'; '.join(issue['likely_causes'])}")
    lines.append(f"   - Next: {code(issue['next_command'])}")
    return "\n".join(lines)


def render(result: dict, issues: Sequence[dict] | None = None, notes: Sequence[str] = (), full: bool = False,
           lead: str = "") -> str:
    shown = result["issues"] if issues is None else list(issues)
    parts = [p for p in (lead, headline(result)) if p]
    if not shown:
        scope = "; ".join(p for p in (group.pods_phrase(result["counts"]), group.nodes_phrase(result["counts"])) if p)
        parts.append("No unhealthy objects in what you pasted" + (f": {scope}." if scope else "."))
    else:
        parts.append("Issues: " + _found_phrase(shown))
        parts.append("\n".join(render_issue(i, n, full) for n, i in enumerate(shown, start=1)))
    parts.append(footer(result, notes))
    return "\n\n".join(parts)


def _by_severity(issues: Sequence[dict]) -> dict:
    return {s: sum(1 for i in issues if i["severity"] == s) for s in group.SEVERITY_ORDER}


def _found_phrase(issues: Sequence[dict]) -> str:
    by = _by_severity(issues)
    return ", ".join(f"{n} {s}" for s, n in by.items() if n) + "."


def footer(result: dict, notes: Sequence[str] = ()) -> str:
    lines = list(notes)
    parsed = [f"{b['label']} ({b['rows']})" for b in result["parsed_blocks"]]
    if parsed:
        lines.append("Parsed: " + ", ".join(parsed) + ".")
    if result["skipped_rows"]:
        lines.append(f"{result['skipped_rows']} line(s) inside tables could not be read as rows and were skipped.")
    lines += [f"Not recognised: {u}." for u in result["unparsed_blocks"]]
    lines += [f"Not assessed: {n}." for n in result["not_assessed"]]
    th = result["thresholds"]
    lines.append(f"Thresholds: restart storm {th['restart_storm']} restarts within {th['recent_restart_min']}m, "
                 f"Pending {th['pending_min']}m, pressure {th['pressure_pct']}%, recent rollout "
                 f"{th['recent_rollout_h']}h.")
    lines.append("Read-only: nothing was changed.")
    return "\n".join(f"_{line}_" for line in lines)


def envelope(result: dict) -> dict:
    issues = [{k: i.get(k) for k in ISSUE_FIELDS} for i in result["issues"]]
    return {
        "summary": summary_text(result), "items": issues, "confidence": analysis.confidence(result),
        "needs_human": analysis.needs_human(result), "issues": issues, "cluster": result.get("label") or None,
        "as_of": "paste", "cluster_summary": result["counts"], "parsed_blocks": result["parsed_blocks"],
        "unparsed_blocks": result["unparsed_blocks"], "not_assessed": result["not_assessed"],
        "thresholds": result["thresholds"], "read_only": True,
    }


def as_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=2) + "\n```"
