"""Rendering in code: the proposal table, the exact issue text that gets written, the create cards and the
final report. Markdown only, no raw HTML or images; links only from tool output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from proposal import needs_split
from records import Project, ProposedIssue, Spec, Team

EDIT_HINT = ('Reply with edits ("drop 4", "estimate 2 = 3", "retitle 2: New title", "merge 2,3", "team Name", '
             '"project Name") or pick which issues to create.')


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _est(issue: ProposedIssue) -> str:
    return "?" if issue.estimate is None else str(issue.estimate)


def issue_notes(issue: ProposedIssue) -> str:
    notes = []
    if issue.estimate is None:
        notes.append("needs refinement")
    if issue.exists:
        notes.append(f"exists: {issue.exists} (skipped)")
    if needs_split(issue):
        notes.append("consider splitting")
    if issue.status == "created":
        notes.append(f"created: {issue.identifier or 'yes'}")
    return "; ".join(notes)


def headline(spec: Spec, team: Team | None, project: Project | None, issues: Sequence[ProposedIssue]) -> str:
    where = f"team {team.name}" if team else "no team"
    if project is not None:
        where += f', {"existing" if project.id else "new"} project "{project.name}"'
    exists = sum(1 for i in issues if i.exists)
    unclear = sum(1 for i in issues if i.estimate is None)
    return (f'Spec "{spec.title}" -> {where}: {len(issues)} proposed issues ({exists} already exist, '
            f"{unclear} need refinement).")


def table(issues: Sequence[ProposedIssue]) -> str:
    rows = ["| # | Title | Est | Depends on | Notes |", "|---|---|---|---|---|"]
    for issue in issues:
        deps = ", ".join(str(d) for d in issue.depends_on) or "-"
        rows.append(f"| {issue.local_id} | {_cell(issue.title)} | {_est(issue)} | {deps} | {_cell(issue_notes(issue))} |")
    return "\n".join(rows)


def proposal(spec: Spec, team: Team | None, project: Project | None, issues: Sequence[ProposedIssue],
             notes: Sequence[str], can_create: bool) -> str:
    parts = [headline(spec, team, project, issues), table(issues)]
    parts.append(EDIT_HINT if can_create else "Nothing will be created from here: copy the issues below.")
    if not can_create:
        by_id = {i.local_id: i for i in issues}
        parts.extend(plain_issue(i, by_id) for i in issues)
    parts.extend(n for n in notes if n)
    return "\n\n".join(parts)


def issue_text(issue: ProposedIssue, by_id: Mapping[int, ProposedIssue], estimate_footer: bool) -> str:
    """The exact description written to the tracker: what, acceptance lines, dependencies, source quote."""
    lines = [issue.description.strip(), ""]
    if issue.acceptance_criteria:
        lines += ["Acceptance criteria:", *(f"- {a}" for a in issue.acceptance_criteria)]
    else:
        lines.append("Acceptance criteria: not stated in spec")
    for dep in issue.depends_on:
        other = by_id.get(dep)
        if other is not None:
            ref = f" ({other.identifier})" if other.identifier else ""
            lines.append(f"Depends on: {other.title}{ref}")
    if estimate_footer and issue.estimate is not None:
        lines.append(f"Estimate: {issue.estimate}")
    if issue.estimate is None:
        lines.append("Estimate: needs refinement")
    lines += ["", f'Source: "{issue.source_quote}"']
    return "\n".join(lines).strip()


def plain_issue(issue: ProposedIssue, by_id: Mapping[int, ProposedIssue]) -> str:
    return f"**#{issue.local_id} {issue.title}**\n\n{issue_text(issue, by_id, estimate_footer=True)}"


def issue_card(n: int, total: int, title: str, description: str, estimate: int | None, team: Team,
               project: Project | None) -> str:
    est = "not set (needs refinement)" if estimate is None else str(estimate)
    where = f"Team: {team.name}" + (f" | Project: {project.name}" if project else " | No project")
    return (f"**Create issue {n} of {total}?**\n\n{where} | Estimate: {est}\n\n**Title:** {title}\n\n"
            f"**Description:**\n\n{description}\n\nCreate, Skip or Cancel. To change it first, reply "
            f'"title: ..." or "description: ...".')


def project_card(project: Project, team: Team, count: int) -> str:
    return (f'**Create project "{project.name}"?**\n\nTeam: {team.name}. The {count} picked issue(s) will go in '
            "it. Create, Skip (create the issues without a project) or Cancel.")


def report(issues: Sequence[ProposedIssue], picked: Sequence[int], project_line: str, notes: Sequence[str]) -> str:
    chosen = [i for i in issues if i.local_id in set(picked)]
    created = [i for i in chosen if i.status == "created"]
    lines = [f"Created {len(created)} of {len(chosen)} picked issue(s)."]
    if project_line:
        lines.append(project_line)
    for issue in chosen:
        if issue.status == "created":
            ref = f"[{issue.identifier or 'link'}]({issue.url})" if issue.url else (issue.identifier or "created")
            lines.append(f"- #{issue.local_id} {issue.title}: created {ref}")
        elif issue.status == "skipped":
            lines.append(f"- #{issue.local_id} {issue.title}: skipped"
                         + (f" (already exists as {issue.exists})" if issue.exists else ""))
        elif issue.status == "failed":
            lines.append(f"- #{issue.local_id} {issue.title}: not created")
        else:
            lines.append(f"- #{issue.local_id} {issue.title}: not reached")
    return "\n".join([*lines, *(n for n in notes if n)])
