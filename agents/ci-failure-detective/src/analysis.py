"""Pure: the facts one diagnosis gathers (`Analysis`, per turn, never persisted) and the `Report` kept in
the conversation state for follow-ups and the approved writes (JSON-safe)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from culprit import Commit, Narrowed
from excerpt import Location
from history import Flakiness, Row
from records import Job, PullRequest, Run
from targets import Target


@dataclass(frozen=True)
class Analysis:
    target: Target
    pr: PullRequest | None = None
    run: Run | None = None
    job: Job | None = None
    other_jobs: tuple[str, ...] = ()
    log_raw: str = ""
    log_source: str = ""              # "log" | "annotations" | ""
    excerpt: str = ""
    test_ids: tuple[str, ...] = ()
    locations: tuple[Location, ...] = ()
    failure_class: str = "unknown"
    class_line: str = ""
    commits: tuple[Commit, ...] = ()  # oldest first: the pull request's commits, or the suspect range
    rows: tuple[Row, ...] = ()
    flaky: Flakiness | None = None
    change_shas: tuple[str, ...] = ()  # commits that changed the failing files (blame)
    narrowed: Narrowed | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def test_id(self) -> str:
        return self.test_ids[0] if self.test_ids else ""

    @property
    def target_branch(self) -> str:
        if self.pr is not None and self.pr.base_branch:
            return self.pr.base_branch
        return self.run.branch if self.run else self.target.branch

    @property
    def head_branch(self) -> str:
        return self.pr.head_branch if self.pr is not None else ""


@dataclass(frozen=True)
class Report:
    """What a follow-up or a write needs from the last diagnosis in this conversation."""

    owner: str = ""
    repo: str = ""
    pr_number: str = ""
    run_id: str = ""
    run_url: str = ""
    workflow: str = ""
    job_name: str = ""
    sha: str = ""
    verdict: str = ""            # real | flaky | infra | unclear
    culprit_sha: str = ""
    culprit_login: str = ""
    test_id: str = ""
    rate: str = ""
    card: str = ""
    excerpt: str = ""
    comment: str = ""            # the pull request comment body (markdown)
    ticket_title: str = ""
    ticket_body: str = ""
    ticket_update: str = ""      # the comment for an existing flaky ticket
    other_jobs: tuple[str, ...] = ()
    target: dict[str, Any] = field(default_factory=dict)

    @property
    def repo_full(self) -> str:
        return f"{self.owner}/{self.repo}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["other_jobs"] = list(self.other_jobs)
        return data

    @staticmethod
    def from_dict(data: object) -> Report | None:
        if not isinstance(data, dict) or not data.get("run_id"):
            return None
        values = {f.name: data.get(f.name) for f in fields(Report) if f.name in data}
        values["other_jobs"] = tuple(str(x) for x in values.get("other_jobs") or ())
        values["target"] = dict(values.get("target") or {})
        return Report(**{k: (str(v) if k not in ("other_jobs", "target") and v is not None else v)
                         for k, v in values.items() if v is not None})
