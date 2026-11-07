"""Pure: the destination half of the send guard for code-host writes (copied send_guard.py stays a
copy plus a call into this). An approved comment or issue names one repository, one issue or pull
request number (none for a create), one title and a set of labels; a write call must name exactly those.
"""

from __future__ import annotations

from collections.abc import Callable

# Destination and ticket fields bound by the approval (exact keys, case-insensitive).
OWNER_KEYS = frozenset({"owner", "repo_owner", "org", "organization", "namespace"})
REPO_KEYS = frozenset({"repo", "repository", "repo_name", "full_name", "repository_name"})
NUMBER_KEYS = frozenset({"issue_number", "pull_number", "pr_number", "number", "issue_id",
                         "pull_request_number", "issue_key", "key"})
PROJECT_KEYS = frozenset({"project", "project_key", "project_id"})
TITLE_KEYS = frozenset({"title", "summary"})
LABEL_KEYS = frozenset({"labels", "label"})
BOUND_KEYS = OWNER_KEYS | REPO_KEYS | NUMBER_KEYS | PROJECT_KEYS | TITLE_KEYS | LABEL_KEYS


def binding_mismatch(fields: list[tuple[str, str]], approval, normalize: Callable[[str], str]) -> str | None:
    """Destination, title and labels of a code-host write: every such field must be the approved
    value, and the repository (plus the number, for a comment) must be named at all."""
    owner, repo = approval.owner.casefold(), approval.repo.casefold()
    full = f"{owner}/{repo}"
    owners = [v.casefold() for k, v in fields if k in OWNER_KEYS]
    repos = [v.casefold() for k, v in fields if k in REPO_KEYS]
    if not repos or any(r not in (repo, full) for r in repos) or any(o != owner for o in owners):
        return f"Refused: this call must write to exactly the approved repository ({approval.owner}/{approval.repo})."
    numbers = [number_text(v) for k, v in fields if k in NUMBER_KEYS]
    if approval.number and (not numbers or any(n != approval.number for n in numbers)):
        return f"Refused: this call must target exactly the approved issue or pull request (#{approval.number})."
    if not approval.number and numbers:
        return "Refused: the approved write creates a new item; it names no existing issue or pull request."
    projects = [v.casefold() for k, v in fields if k in PROJECT_KEYS]
    if any(p != approval.project.casefold() for p in projects):
        return f"Refused: this call must write to exactly the approved project ({approval.project or 'none'})."
    titles = [normalize(v) for k, v in fields if k in TITLE_KEYS]
    if (approval.title and (not titles or any(t != normalize(approval.title) for t in titles))) or (
            not approval.title and titles):
        return f"Refused: the title must be exactly the approved title ({approval.title or 'none'})."
    approved_labels = {label.casefold() for label in approval.labels}
    if any(v.casefold() not in approved_labels for k, v in fields if k in LABEL_KEYS):
        return "Refused: this call adds a label the user did not approve."
    return None


def number_text(value: object) -> str:
    text = str(value if value is not None else "").strip().lstrip("#")
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text
