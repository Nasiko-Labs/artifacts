"""Pure: everything the user reads, rendered by code: the proposal above card 1, each later card's text, the
final report, leftovers, and the envelope (with `confidence` and `needs_human` computed here). Markdown only,
no raw HTML, no images; the only URL ever shown is the pull request URL read from tool output."""

from __future__ import annotations

import json
from collections.abc import Sequence

import tests_runner as tr
from diffing import FileChange, full_diff
from writeplan import Step, WritePlan

MAX_DIFF_CHARS = 30_000
HIGH_MAX_FILES = 3
HIGH_MAX_LINES = 60


def diff_block(items: Sequence[FileChange]) -> str:
    text = full_diff(list(items))
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + "\n[... diff truncated for display; the full diff is in the data part ...]\n"
    return "```diff\n" + text.replace("```", "`\u200b``") + "```"


def _changes(state: dict) -> list[FileChange]:
    return [FileChange.from_dict(c) for c in state.get("changes") or []]


def confidence(state: dict) -> str:
    record = state.get("localization") or {}
    loc = record.get("confidence") or "low"
    tests = tr.Tests.from_dict(state.get("tests"))
    items = _changes(state)
    warnings = state.get("warnings") or []
    danger = any("adds `" in w for w in warnings)
    if loc == "low" or tests.result == "failed" or danger or state.get("timed_out"):
        return "low"
    lines = sum(c.changed for c in items)
    proven = tests.pre_existing_failures or any(c.new_file for c in items)
    if loc == "high" and tests.result == "passed" and proven and len(items) <= HIGH_MAX_FILES \
            and lines <= HIGH_MAX_LINES and not warnings:
        return "high"
    return "medium"


def envelope(state: dict, label: str, pr_url: str | None = None) -> dict:
    items = _changes(state)
    plan = WritePlan.from_dict(state.get("plan"))
    branch = plan.steps[0].branch if plan and pr_url else None
    files = [{"path": c.path, "added": c.added, "removed": c.removed} for c in items]
    record = state.get("localization") or {}
    env = {
        "summary": "", "items": [{**f, "new_file": c.new_file} for f, c in zip(files, items, strict=True)],
        "pr_url": pr_url, "branch": branch,
        "base": state.get("ref"), "draft": True, "diff": full_diff(items), "files_changed": files,
        "tests": tr.Tests.from_dict(state.get("tests")).to_dict(),
        "localization": {k: record.get(k) for k in ("confidence", "suspect_files", "edit_locations") if k in record},
        "warnings": list(state.get("warnings") or []) + [f"`{x}` not changed: {w}" for x, w in
                                                           state.get("discarded") or []],
        "state": label,
        "confidence": confidence(state) if items else "low",
        "needs_human": bool(items) and label not in ("no_change",),
    }
    if label in ("no_change", "refused") and not items:
        env["needs_human"] = False
    env["summary"] = summary(env, state)
    return env


def summary(env: dict, state: dict) -> str:
    files = env["files_changed"]
    added = sum(f["added"] for f in files)
    removed = sum(f["removed"] for f in files)
    tests = tr.Tests.from_dict(state.get("tests"))
    test_part = f"Tests: {tests.summary}." if tests.ran else f"Tests not run ({tests.reason or 'none'})."
    size = f"{len(files)} file{'s' if len(files) != 1 else ''}, +{added} −{removed}"
    label = env["state"]
    if label == "opened":
        return f"Draft PR opened: {env['pr_url'] or 'URL not returned by the code host'} ({size}). {test_part}"
    if label == "awaiting_approval":
        return f"Proposed change awaiting approval ({size}). {test_part}"
    if label == "diff_only":
        return f"Diff only, no pull request ({size}). {test_part}"
    if label == "skipped":
        return "Stopped before every step ran. " + (state.get("leftovers") or "")
    if label == "no_change":
        return "The engine made no change."
    return "No pull request: the change was refused." if not files else f"Refused ({size})."


def as_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"


def _header(state: dict, items: Sequence[FileChange]) -> str:
    repo = state.get("repo") or {}
    where = f"{repo.get('owner')}/{repo.get('name')}" if repo else "the pasted files"
    number = state.get("issue_number")
    ref = f"{where}#{number}" if number is not None else where
    added, removed = sum(c.added for c in items), sum(c.removed for c in items)
    tests = tr.Tests.from_dict(state.get("tests"))
    test_part = f"tests: {tests.summary}, ran in a sandbox" if tests.ran else f"tests not run: {tests.reason}"
    loc = (state.get("localization") or {}).get("confidence") or "unknown"
    return (f"**Proposed change for {ref}** — {len(items)} file{'s' if len(items) != 1 else ''}, +{added} −{removed} "
            f"(localization {loc}; {test_part}).")


def _notes(state: dict) -> list[str]:
    out = [f"- ⚠ {w}" for w in state.get("warnings") or []]
    out += [f"- `{x}` was not changed: {w}" for x, w in state.get("discarded") or []]
    out += [f"- `{x}` was not given to the engine: {w}" for x, w in state.get("excluded") or []]
    if tr.Tests.from_dict(state.get("tests")).pre_existing_failures:
        out.append("- Some tests were already failing before the change.")
    if state.get("timed_out"):
        out.append("- The engine hit its time limit; this is a partial change.")
    out += [f"- Coverage: {line}" for line in state.get("coverage") or [] if not line.startswith("no repository")]
    if state.get("done_line"):
        out.append(f"- The engine's own note (unverified): {state['done_line']}")
    return out


def proposal(state: dict, plan: WritePlan | None, title: str, body: str) -> str:
    items = _changes(state)
    lines = [_header(state, items), f"**PR title:** {title}", "", diff_block(items)]
    notes = _notes(state)
    if notes:
        lines += ["", "**Notes**", *notes]
    if plan is not None:
        lines += ["", "**PR body:**", _quote(body), "", "**Plan, each step with its own approval card:**"]
        lines += [f"{i + 1}. {step_line(s)}" for i, s in enumerate(plan.steps)]
        lines += ["", "Nothing has been pushed yet."]
    return "\n".join(lines)


def diff_only(state: dict, title: str, body: str, reason: str) -> str:
    items = _changes(state)
    lines = [_header(state, items), f"**PR title:** {title}", "", diff_block(items)]
    notes = _notes(state)
    if notes:
        lines += ["", "**Notes**", *notes]
    lines += ["", "**PR body (to copy):**", _quote(body), "", f"_{reason} Nothing was pushed._"]
    return "\n".join(lines)


def step_line(step: Step) -> str:
    if step.kind == "branch":
        return f"create branch `{step.branch}` from `{step.base}`"
    if step.kind == "commit":
        return f"commit `{step.path}`" + (" (new file)" if step.new_file else "")
    return f"open a **draft** pull request `{step.title}` from `{step.branch}` to `{step.base}`"


def card_header(step: Step, repo: str) -> str:
    if step.kind == "branch":
        return f"Create branch {step.branch} in {repo} from {step.base}?"
    if step.kind == "commit":
        return f"Commit {step.path} to {step.branch}?"
    return f"Open draft PR \"{step.title}\" from {step.branch} to {step.base}?"


def card_text(plan: WritePlan, index: int, state: dict, note: str = "") -> str:
    step = plan.steps[index]
    lines = [note, ""] if note else []
    lines.append(f"**Step {index + 1} of {len(plan.steps)}:** {step_line(step)}.")
    if step.kind == "commit":
        change = next((c for c in _changes(state) if c.path == step.path), None)
        if change is not None:
            lines += ["", diff_block([change])]
        lines += ["", f"Commit message: `{step.message.splitlines()[0] if step.message else ''}`"]
    if step.kind == "pr":
        lines += ["", f"**Title:** {step.title}", "", _quote(step.body)]
    lines += ["", "Approve runs exactly this one write; Skip stops here."]
    return "\n".join(lines)


def written_note(step: Step) -> str:
    if step.kind == "branch":
        return f"Created branch `{step.branch}`."
    if step.kind == "commit":
        return f"Committed `{step.path}`."
    return "Opened the draft pull request."


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in (text or "").split("\n"))
