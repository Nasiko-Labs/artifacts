"""Pure: the test plan. Code-made steps come first (one per changed test file, "add or update tests" when source
changed without tests, commands only when the user pasted one or the diff shows the runner, one manual step per
risk surface); the model's checked manual steps follow. At most 8 checkboxes, never ticked."""

from __future__ import annotations

import re

from facts import Facts

MAX_STEPS = 8
CHECKBOX = "- [ ] "
_TICK = re.compile(r"`([^`]+)`")


def code_steps(facts: Facts) -> list[str]:
    steps = [f"Run the tests in `{path}`" for path in facts.test_files]
    no_tests = next((s for s in facts.surfaces if s.name == "no_tests"), None)
    if no_tests is not None:
        target = next(iter(facts.new_symbols + facts.changed_symbols), None) or no_tests.token
        steps.insert(0, f"Add or update tests for `{target}`")
    steps += [f"Run `{command}`" for command in facts.test_commands]
    for surface in facts.surfaces:
        if surface.name == "migration":
            steps.append(f"Apply the migration `{surface.token}` on a copy of the database and roll it back")
        elif surface.name == "config" and surface.token in facts.env_vars + facts.options:
            steps.append(f"Start with `{surface.token}` unset and check the default behaviour")
        elif surface.name == "removed_public":
            steps.append(f"Grep for `{surface.token.split(' ')[-1]}` to confirm no caller remains")
    steps += [f"Call `{route}` with a valid and an invalid payload" for route in facts.new_routes]
    return _dedupe(steps)


def _dedupe(steps: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for step in steps:
        key = " ".join(step.casefold().split()).rstrip(".")
        if key not in seen:
            seen.add(key)
            out.append(step.rstrip("."))
    return out


def _covered(step: str, code: list[str]) -> bool:
    """A model step whose every backticked name is already in one code step repeats it."""
    names = _TICK.findall(step)
    return bool(names) and any(all(f"`{n}`" in c for n in names) for c in code)


def plan(code: list[str], model: list[str]) -> list[str]:
    """The checkbox lines: code steps, then the model's checked steps, deduplicated, at most MAX_STEPS."""
    own = [s.removeprefix(CHECKBOX).removeprefix("- ").removeprefix("[ ] ").strip() for s in model]
    merged = _dedupe([*code, *(s for s in own if not _covered(s, code))])
    return [CHECKBOX + step for step in merged[:MAX_STEPS]]


def tested_line(facts: Facts) -> str:
    return f"Tested (as you said): {facts.tested}" if facts.tested else "Tested: not stated"
