"""Pure: the safety rules every edit passes before it is applied. A refusal is an error observation.

1. Fix the code, not the test: a test edit needs reason `test_is_wrong` and a quote found in the pasted
   tests; the user's "don't change the test" forbids it outright.
2. Assertion weakening (any test edit): fewer asserts, skips/xfail/early return/try around an assert, a
   broader `pytest.raises`, a deleted test or parametrize case, a widened tolerance.
3. Special-casing (source edits): an added comparison/branch/key holding one of the failing test's
   literals is flagged (a warning, not a refusal).
4. Size: at most MAX_CHANGED_LINES changed lines in at most MAX_FILES_CHANGED files.
5. No shell, network, eval or file writes added unless the original file already had that text.
6. Only pasted files; config files that change how tests run are never edited."""

from __future__ import annotations

import re
from dataclasses import dataclass

import files as f
from actions import Action
from inputs import Constraints, appears_in
from patch import changed_lines, stats

MAX_CHANGED_LINES = 40
MAX_FILES_CHANGED = 3

_ASSERTS = re.compile(r"\bassert\b|self\.assert\w*|pytest\.raises|pytest\.approx|expect\(")
_SKIPS = re.compile(r"pytest\.mark\.skip|pytest\.skip\(|xfail|importorskip|unittest\.skip|\bif False\b|"
                    r"\bif 0\s*:")
_BROAD_RAISES = re.compile(r"pytest\.raises\(\s*(?:Exception|BaseException)\s*[,)]")
_TOLERANCE = re.compile(r"\b(rel|abs|delta|places)\s*=\s*([\d.eE+-]+)")
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def test", re.MULTILINE)
_PARAM_CASE = re.compile(r"^\s*(?:\(|pytest\.param\()", re.MULTILINE)
_DANGER = re.compile(r"os\.system|subprocess|socket|requests\.|urllib|eval\(|exec\(|__import__|"
                     r"open\(.*[\"'][wax+]")
_BRANCH = re.compile(r"\b(?:if|elif|while|case)\b|==|!=|\bin\b|^\s*[\"'][^\"']*[\"']\s*:")
_STRING = re.compile(r"\"((?:[^\"\\\n]|\\.){2,})\"|'((?:[^'\\\n]|\\.){2,})'")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_TRIVIAL = {"0", "1", "-1", "2"}


@dataclass(frozen=True)
class Verdict:
    refusal: str | None = None
    test_edit: bool = False
    special_casing: bool = False


def is_test_path(path: str, test_files: tuple[str, ...]) -> bool:
    return path in test_files or f.is_test_file(path)


def check(action: Action, updated: str, current: dict[str, str], originals: dict[str, str],
          test_files: tuple[str, ...], rules: Constraints) -> Verdict:
    path = action.path
    if f.is_config(path):
        return Verdict(f"refused: {path} changes how tests run; it is never edited")
    if rules.only_edit and not any(path == p or path.endswith("/" + p) for p in rules.only_edit):
        return Verdict(f"refused: the user allowed edits only to {', '.join(rules.only_edit)}")
    if is_test_path(path, test_files):
        refusal = _test_refusal(action, originals, test_files, rules)
        if refusal:
            return Verdict(refusal)
    else:
        danger = _danger(action.old, action.new, originals.get(path, ""))
        if danger:
            return Verdict(f"refused: the edit adds `{danger}` (shell, network, eval or file writes)")
    after = {**current, path: updated}
    if changed_lines(originals, after) > MAX_CHANGED_LINES or len(stats(originals, after)) > MAX_FILES_CHANGED:
        return Verdict(f"refused: smaller change, please (at most {MAX_CHANGED_LINES} changed lines in "
                       f"{MAX_FILES_CHANGED} files)")
    test_edit = is_test_path(path, test_files)
    flagged = not test_edit and special_casing(action.old, action.new, originals, test_files)
    return Verdict(None, test_edit, flagged)


def _test_refusal(action: Action, originals: dict[str, str], test_files: tuple[str, ...],
                  rules: Constraints) -> str | None:
    if rules.no_test_edits:
        return "refused: the user said not to change the test; fix the source code"
    tests_text = "\n".join(originals.get(p, "") for p in test_files)
    if action.target != "test" or action.reason != "test_is_wrong" or not appears_in(action.quote, tests_text):
        return ("refused: fix the code, not the test (a test edit needs \"target\": \"test\", \"reason\": "
                "\"test_is_wrong\" and an exact \"quote\" from the tests that shows it)")
    weakened = weakening(action.old, action.new)
    return f"refused: the edit weakens the test ({weakened})" if weakened else None


def weakening(old: str, new: str) -> str | None:
    """Why replacing `old` by `new` in a test weakens it, or None."""
    if len(_ASSERTS.findall(new)) < len(_ASSERTS.findall(old)):
        return "fewer assertions"
    added_skip = set(_SKIPS.findall(new)) - set(_SKIPS.findall(old))
    if added_skip:
        return f"adds `{min(added_skip)}`"
    if _BROAD_RAISES.search(new) and not _BROAD_RAISES.search(old) and "pytest.raises" in old:
        return "broadens pytest.raises to a generic exception"
    if re.search(r"^\s*return\b", new, re.MULTILINE) and not re.search(r"^\s*return\b", old, re.MULTILINE):
        return "adds a return before the assertions"
    if new.count("try:") > old.count("try:") and _ASSERTS.search(new):
        return "wraps an assertion in try/except"
    if len(_TEST_DEF.findall(new)) < len(_TEST_DEF.findall(old)):
        return "deletes a test"
    if "parametrize" in old and len(_PARAM_CASE.findall(new)) < len(_PARAM_CASE.findall(old)):
        return "deletes a parametrize case"
    return _widened(old, new)


def _widened(old: str, new: str) -> str | None:
    before = {k: float(v) for k, v in _TOLERANCE.findall(old) if _num(v)}
    for key, value in _TOLERANCE.findall(new):
        if key in before and _num(value):
            wider = float(value) < before[key] if key == "places" else float(value) > before[key]
            if wider:
                return f"widens the tolerance `{key}`"
    return None


def _num(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _danger(old: str, new: str, original: str) -> str | None:
    added = [line for line in new.splitlines() if line not in old.splitlines()]
    for line in added:
        match = _DANGER.search(line)
        if match and match.group(0) not in original:
            return match.group(0)
    return None


def test_literals(originals: dict[str, str], test_files: tuple[str, ...]) -> set[str]:
    text = "\n".join(originals.get(p, "") for p in test_files)
    strings = {a or b for a, b in _STRING.findall(text)}
    numbers = {n for n in _NUMBER.findall(text) if n not in _TRIVIAL and (len(n.lstrip("-")) >= 2 or "." in n)}
    return strings | numbers


def special_casing(old: str, new: str, originals: dict[str, str], test_files: tuple[str, ...]) -> bool:
    literals = test_literals(originals, test_files)
    added = [line for line in new.splitlines() if line not in old.splitlines()]
    for line in added:
        if not _BRANCH.search(line):
            continue
        if any(_has_literal(line, lit) for lit in literals):
            return True
    return False


def _has_literal(line: str, literal: str) -> bool:
    if literal.lstrip("-").replace(".", "", 1).isdigit():
        return bool(re.search(rf"(?<![\w.]){re.escape(literal)}(?![\w.])", line))
    return f'"{literal}"' in line or f"'{literal}'" in line
