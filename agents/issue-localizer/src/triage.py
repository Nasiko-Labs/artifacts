"""Pure: the issue kind (code overrides first; the model's label only when they don't decide), the
missing-information checklist with the exact question to ask the reporter, and the next step built from a
template filled with real paths."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

KINDS = ("bug", "feature", "question", "docs", "chore", "unclear")
NEEDS_INFO_AT = 3

_TRACEBACK = re.compile(r"Traceback \(most recent call last\)|\b\w*(?:Error|Exception)\b:|\bpanic:|\bat [\w.$<>]+ \(",
                        re.IGNORECASE)
_STEPS = re.compile(r"(?im)^\s*(?:\d+[.)]|[-*])\s+\S|\bsteps?\b|\breproduc|```")
_EXPECTED = re.compile(r"\bexpected\b|\bactual\b|\bshould\b|\binstead of\b", re.IGNORECASE)
_VERSION = re.compile(r"(?:\bv(?:ersion)?\s*:?\s*\d+(?:\.\d+)+|\b\d+\.\d+(?:\.\d+)?\b.{0,20}\bversion\b|"
                      r"\bversion\b.{0,20}\b\d+\.\d+)", re.IGNORECASE)
_ENVIRONMENT = re.compile(r"\b(?:windows|macos|mac os|linux|ubuntu|debian|android|ios|chrome|firefox|safari|node|"
                          r"python|java|docker|kubernetes)\b", re.IGNORECASE)
_LOGS = re.compile(r"Traceback|\b\w*(?:Error|Exception)\b|\blog\b|\bstack\b|```", re.IGNORECASE)
_EXAMPLE = re.compile(r"```|\binput\b|\bexample\b|\bcurl\b|\brequest\b", re.IGNORECASE)
_AREA = re.compile(r"[\w-]+\.[a-z]{1,5}\b|`[^`]+`|/api/|\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z]\w+\b")

_LABEL_KIND = (
    (("bug", "defect", "regression"), "bug"),
    (("enhancement", "feature", "feature request"), "feature"),
    (("question", "help wanted", "support"), "question"),
    (("documentation", "docs"), "docs"),
    (("chore", "maintenance", "refactor", "dependencies"), "chore"),
)


@dataclass(frozen=True)
class Missing:
    item: str
    ask: str


def kind_override(title: str, body: str, labels: Sequence[str], candidate_paths: Sequence[str]) -> str | None:
    """The kind decided by code, or None when the model's label is needed."""
    lowered = {label.casefold().strip() for label in labels}
    if _TRACEBACK.search(body) or lowered & {"bug", "defect"}:
        return "bug"
    if lowered & {"enhancement", "feature"} or re.match(r"\s*feat\b", title, re.IGNORECASE) \
            or "feature request" in title.casefold():
        return "feature"
    if lowered & {"question", "help wanted"} or (title.strip().endswith("?") and not _LOGS.search(body)):
        return "question"
    if "documentation" in lowered or (candidate_paths and all(p.endswith(".md") for p in candidate_paths)):
        return "docs"
    for names, kind in _LABEL_KIND:
        if lowered & set(names):
            return kind
    return None


def clean_kind(raw: object) -> str:
    text = str(raw or "").casefold().strip()
    return text if text in KINDS else "unclear"


def missing_info(title: str, body: str, kind: str) -> tuple[Missing, ...]:
    text = f"{title}\n{body}"
    out = []
    if not _STEPS.search(body):
        out.append(Missing("steps to reproduce", "What exact steps (inputs, clicks, commands) trigger it?"))
    if not _EXPECTED.search(text):
        out.append(Missing("expected vs actual", "What did you expect to happen, and what happened instead?"))
    if not (_VERSION.search(text) or _ENVIRONMENT.search(text)):
        out.append(Missing("version / environment", "Which version, OS and runtime are you on?"))
    if kind in ("bug", "unclear") and not _LOGS.search(text):
        out.append(Missing("logs / stack trace", "Can you paste the error message or stack trace?"))
    if kind in ("bug", "unclear") and not _EXAMPLE.search(text):
        out.append(Missing("minimal example", "Can you share a minimal input or request that shows it?"))
    if not _AREA.search(text):
        out.append(Missing("affected area", "Which page, endpoint, command or file is affected?"))
    return tuple(out)


def needs_info(kind: str, missing: Sequence[Missing]) -> bool:
    return kind in ("bug", "unclear") and len(missing) >= NEEDS_INFO_AT


def test_file_for(path: str, listing: Sequence[str]) -> str | None:
    """A listed test file for `path`'s module: tests/test_<m>.py, <m>.test.ts, <m>_test.go, <M>Test.java ..."""
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    wanted = {f"test_{stem}.py", f"{stem}_test.py", f"{stem}.test.ts", f"{stem}.test.js", f"{stem}.spec.ts",
              f"{stem}.spec.js", f"{stem}.test.tsx", f"{stem}_test.go", f"{stem}Test.java", f"{stem}Test.kt",
              f"{stem}_spec.rb", f"{stem}_test.rb", f"{stem}Tests.cs"}
    found = [p for p in listing if p.rsplit("/", 1)[-1] in wanted]
    return found[0] if found else None


def tests_related(paths: Sequence[str], listing: Sequence[str]) -> tuple[str, ...]:
    found = [t for t in (test_file_for(p, listing) for p in paths) if t]
    return tuple(dict.fromkeys(found))


def next_step(kind: str, missing: Sequence[Missing], top: dict | None, listing: Sequence[str]) -> str:
    if needs_info(kind, missing):
        return "Ask the reporter for: " + ", ".join(m.item for m in missing) + "."
    if top is None:
        return "Paste the relevant files (`# file: path`) or point me at a directory (\"look in src/api\")."
    where = f"`{top['path']}`"
    if top.get("symbol"):
        where += f" at `{top['symbol']}` (L{top['start_line']}–L{top['end_line']})"
    if kind == "feature":
        return f"Design the change in {where}."
    if kind in ("question", "docs"):
        return f"Answer from {where}."
    test = test_file_for(top["path"], listing)
    test_part = f"add a failing test in `{test}`" if test else "add a failing test (no test file for it is listed: create one)"
    steps = "the steps still missing from the report" if any(m.item == "steps to reproduce" for m in missing) \
        else "the stated steps"
    return f"Reproduce with {steps}; start in {where}; {test_part}."
