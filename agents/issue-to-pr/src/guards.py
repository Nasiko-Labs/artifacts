"""Pure: the rules every change passes after the engine ran. A file that breaks a rule has its edits
discarded with the reason shown; a secret refuses the whole pull request; an over-size change gets no card.

1. Allow-list: a fetched original, or ONE new file next to an existing test file / in a listed test dir.
   Never dotfiles, CI, vendored, lock, generated, minified, binary or > 200 KB files.
2. Size: <= 12 files, <= 800 changed lines, <= 500 in one file.
3. Secrets in added lines refuse everything (masked).
4. Dangerous additions not already in the original: a warning and needs_human, never a refusal.
5. No test weakening in an edited test file (unless the issue itself says the test is wrong: a warning).
6. The user's constraints: no test changes, only under a directory, no new files.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

import paths as p
import secrets_scan
from constraints import Constraints
from diffing import FileChange

MAX_FILES = 12
MAX_CHANGED_LINES = 800
MAX_FILE_CHANGED_LINES = 500
MAX_NEW_FILES = 1

_DANGER = re.compile(r"curl|wget|nc -|/dev/tcp|bash -i|eval\(|exec\(|child_process|os\.system|"
                     r"subprocess.*shell=True")
_ASSERTS = re.compile(r"\bassert\b|self\.assert\w*|pytest\.raises|expect\(")
_SKIPS = re.compile(r"pytest\.mark\.skip|pytest\.skip\(|xfail|unittest\.skip|\.skip\(|\bxit\(|\bit\.skip")
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def test|^\s*(?:it|test)\(", re.MULTILINE)
_TEST_WRONG = re.compile(r"\btests?\b[^.\n]{0,40}\b(?:is|are) (?:wrong|incorrect|broken)\b|\bwrong (?:test|assertion)\b",
                         re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    kept: tuple[FileChange, ...]
    discarded: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()
    refusal: str | None = None
    too_large: bool = False

    @property
    def changed_lines(self) -> int:
        return sum(c.changed for c in self.kept)


@dataclass
class _Acc:
    kept: list[FileChange] = field(default_factory=list)
    discarded: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def issue_says_test_wrong(issue_text: str) -> bool:
    return bool(_TEST_WRONG.search(issue_text or ""))


def added_lines(change: FileChange) -> list[str]:
    return [ln[1:] for ln in change.diff.split("\n") if ln.startswith("+") and not ln.startswith("+++")]


def check(items: Sequence[FileChange], originals: Mapping[str, str], listing: Collection[str],
          rules: Constraints, test_is_wrong: bool = False, symlinks: Collection[str] = ()) -> Outcome:
    acc = _Acc()
    acc.discarded += [(s, "symlinks are never written") for s in symlinks]
    new_files = 0
    for change in items:
        why = _file_reason(change, originals, listing, rules)
        if why is None and change.new_file:
            new_files += 1
            why = "only one new test file may be added" if new_files > MAX_NEW_FILES else None
        if why is None and p.is_test_file(change.path) and not change.new_file:
            weakened = weakening(originals[change.path], change.final)
            if weakened and not test_is_wrong:
                why = f"the edit weakens a test ({weakened})"
            elif weakened:
                acc.warnings.append(f"`{change.path}`: the edit weakens a test ({weakened}); the issue says the "
                                    "test is wrong, so check it")
        if why is not None:
            acc.discarded.append((change.path, why))
            continue
        danger = _danger(change, originals.get(change.path, ""))
        if danger:
            acc.warnings.append(f"`{change.path}` adds `{danger}` (shell, network or eval): review it closely")
        acc.kept.append(change)
    return _finish(acc)


def _finish(acc: _Acc) -> Outcome:
    secret = [h for c in acc.kept for h in secrets_scan.hits(added_lines(c))]
    if secret:
        return Outcome((), tuple(acc.discarded), tuple(acc.warnings),
                       f"the change adds what looks like a secret ({', '.join(sorted(set(secret)))})")
    total = sum(c.changed for c in acc.kept)
    biggest = max((c.changed for c in acc.kept), default=0)
    too_large = len(acc.kept) > MAX_FILES or total > MAX_CHANGED_LINES or biggest > MAX_FILE_CHANGED_LINES
    return Outcome(tuple(acc.kept), tuple(acc.discarded), tuple(acc.warnings), None, too_large)


def _file_reason(change: FileChange, originals: Mapping[str, str], listing: Collection[str],
                 rules: Constraints) -> str | None:
    path = change.path
    forbidden = p.forbidden_reason(path)
    if forbidden:
        return forbidden
    if "\x00" in change.final:
        return "binary files are never edited"
    if len(change.final.encode("utf-8")) > p.MAX_FILE_BYTES:
        return "the file is over 200 KB"
    if change.new_file and path in listing:
        return "the file was not fetched, so it may not be edited"
    if change.new_file:
        if rules.no_new_files:
            return "you said no new files"
        if not _new_test_allowed(path, listing):
            return "a new file must be a test next to an existing test file"
    elif path not in originals:
        return "the file was not fetched, so it may not be edited"
    if rules.no_test_changes and p.is_test_file(path):
        return "you said not to change tests"
    if rules.only_in and not any(path.startswith(d.rstrip("/") + "/") or path == d for d in rules.only_in):
        return f"you said to change only {', '.join(rules.only_in)}"
    return None


def _new_test_allowed(path: str, listing: Collection[str]) -> bool:
    if not p.is_test_file(path):
        return False
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    beside = any(p.is_test_file(x) and (x.rsplit("/", 1)[0] if "/" in x else "") == folder for x in listing)
    test_dir = p.test_dir_of(path)
    in_dir = test_dir is not None and any(x.startswith(test_dir) for x in listing)
    return beside or in_dir


def _danger(change: FileChange, original: str) -> str | None:
    for line in added_lines(change):
        match = _DANGER.search(line)
        if match and match.group(0) not in original:
            return match.group(0)
    return None


def weakening(old: str, new: str) -> str | None:
    """Why the new test file is weaker than the old one, or None."""
    if len(_ASSERTS.findall(new)) < len(_ASSERTS.findall(old)):
        return "fewer assertions"
    added_skip = set(_SKIPS.findall(new)) - set(_SKIPS.findall(old))
    if added_skip:
        return f"adds `{min(added_skip)}`"
    if len(_TEST_DEF.findall(new)) < len(_TEST_DEF.findall(old)):
        return "deletes a test"
    old_asserts = [ln.strip() for ln in old.splitlines() if _ASSERTS.search(ln)]
    new_asserts = {ln.strip() for ln in new.splitlines() if _ASSERTS.search(ln)}
    changed = [ln for ln in old_asserts if ln not in new_asserts]
    if changed:
        return "changes an existing assertion"
    return None
