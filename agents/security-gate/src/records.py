"""The data model: the pull request, its changed files, one finding, and the gate report. Frozen
dataclasses with dict round-trips (the report lives in the checkpointer between turns). Every value in
a Finding came from the scanner, the secret scan, or pasted findings; the model only adds `why`/`fix`."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low")
SCOPES = ("new", "existing", "unknown_diff")


@dataclass(frozen=True)
class PullRequest:
    repo: str            # owner/name
    number: str
    title: str = ""
    url: str = ""
    head_sha: str = ""
    base_sha: str = ""

    @property
    def label(self) -> str:
        return f"{self.repo} #{self.number}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> PullRequest | None:
        if not isinstance(data, dict) or not data.get("repo") or not data.get("number"):
            return None
        return PullRequest(**{f.name: str(data.get(f.name) or "") for f in fields(PullRequest)})


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str = "modified"     # added | modified | removed | renamed | binary
    patch: str = ""              # unified hunks ("" when the code host sent none)
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class Finding:
    file: str
    start_line: int
    end_line: int
    rule_id: str
    severity: str                # critical | high | medium | low
    message: str
    confidence: str = "MEDIUM"
    cwe: str = ""
    scope: str = "new"           # new | existing | unknown_diff
    snippet: str = ""            # display copy, secrets masked
    why: str = ""
    fix: str = ""
    id: str = ""
    is_secret: bool = False
    ignored: bool = False
    source: str = "scanner"      # scanner | secret scan | pasted

    @property
    def where(self) -> str:
        if self.start_line <= 0:
            return self.file
        span = f"{self.start_line}" if self.end_line <= self.start_line else f"{self.start_line}-{self.end_line}"
        return f"{self.file}:{span}"

    def with_(self, **changes: Any) -> Finding:
        return replace(self, **changes)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> Finding | None:
        if not isinstance(data, dict) or not data.get("file") or not data.get("rule_id"):
            return None
        try:
            start = int(data.get("start_line") or data.get("line") or 0)
            end = int(data.get("end_line") or start)
        except (TypeError, ValueError):
            return None
        severity = str(data.get("severity") or "medium").casefold()
        scope = str(data.get("scope") or "new")
        return Finding(
            file=str(data["file"]), start_line=start, end_line=max(end, start), rule_id=str(data["rule_id"]),
            severity=severity if severity in SEVERITIES else "medium", message=str(data.get("message") or ""),
            confidence=str(data.get("confidence") or "MEDIUM"), cwe=str(data.get("cwe") or ""),
            scope=scope if scope in SCOPES else "new", snippet=str(data.get("snippet") or ""),
            why=str(data.get("why") or ""), fix=str(data.get("fix") or ""), id=str(data.get("id") or ""),
            is_secret=bool(data.get("is_secret")), ignored=bool(data.get("ignored")),
            source=str(data.get("source") or "scanner"))


@dataclass(frozen=True)
class GateReport:
    verdict: str                                   # pass | needs_attention | fail | incomplete
    findings: tuple[Finding, ...] = ()
    pr: PullRequest | None = None
    scanned: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()      # (path, reason)
    notes: tuple[str, ...] = ()                    # coverage lines
    mode: str = "pr"                               # pr | diff | code | findings
    strict: bool = False
    scanner_status: str = "ok"                     # ok | timeout | failed | missing | not_run
    confidence: str = "high"
    needs_human: bool = False
    fragments: bool = False                        # scanned hunks, not whole files
    ignored_rules: tuple[str, ...] = field(default=())
    code_files: int = 0                            # changed files in a scanned language
    skipped_code: int = 0                          # of those, not scanned

    def active(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.ignored)

    def of_scope(self, *scopes: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.active() if f.scope in scopes)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "findings": [f.to_dict() for f in self.findings],
                "pr": self.pr.to_dict() if self.pr else None, "scanned": list(self.scanned),
                "skipped": [list(s) for s in self.skipped], "notes": list(self.notes), "mode": self.mode,
                "strict": self.strict, "scanner_status": self.scanner_status, "confidence": self.confidence,
                "needs_human": self.needs_human, "fragments": self.fragments,
                "ignored_rules": list(self.ignored_rules), "code_files": self.code_files,
                "skipped_code": self.skipped_code}

    @staticmethod
    def from_dict(data: Any) -> GateReport | None:
        if not isinstance(data, dict) or not data.get("verdict"):
            return None
        found = tuple(f for f in (Finding.from_dict(x) for x in data.get("findings") or ()) if f is not None)
        skipped = tuple((str(s[0]), str(s[1])) for s in data.get("skipped") or () if len(s) == 2)
        return GateReport(
            verdict=str(data["verdict"]), findings=found, pr=PullRequest.from_dict(data.get("pr")),
            scanned=tuple(str(x) for x in data.get("scanned") or ()), skipped=skipped,
            notes=tuple(str(x) for x in data.get("notes") or ()), mode=str(data.get("mode") or "pr"),
            strict=bool(data.get("strict")), scanner_status=str(data.get("scanner_status") or "ok"),
            confidence=str(data.get("confidence") or "low"), needs_human=bool(data.get("needs_human")),
            fragments=bool(data.get("fragments")),
            ignored_rules=tuple(str(x) for x in data.get("ignored_rules") or ()),
            code_files=int(data.get("code_files") or 0), skipped_code=int(data.get("skipped_code") or 0))
