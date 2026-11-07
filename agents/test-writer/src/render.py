"""Pure: the markdown reply, rendered by code from the envelope. Model text outside the code block is cleaned
first: no HTML, no images, no links, no headings."""

from __future__ import annotations

import json
import re

from assemble import CHANGE_IMPORT
from checks import REASONED_NOTE
from envelope import fence_lang

FRAMEWORK_NAMES = {"pytest": "pytest", "unittest": "unittest", "jest": "Jest", "vitest": "Vitest", "go": "Go testing"}
MAX_LISTED = 12
_TAG = re.compile(r"<[^>\n]{0,200}>")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def clean(text: str) -> str:
    plain = _LINK.sub(r"\1", _TAG.sub("", str(text or "")))
    return " ".join(plain.replace("**", "").replace("`", "'").split()).lstrip("#> ")


def _names(items: list[str]) -> str:
    return ", ".join(f"`{clean(n)}`" for n in items)


def _listing(items: list[str]) -> str:
    shown = " · ".join(clean(i) for i in items[:MAX_LISTED])
    more = len(items) - MAX_LISTED
    return shown + (f" · and {more} more" if more > 0 else "")


def _code_block(code: str, lang: str) -> str:
    fence = "````" if "```" in code else "```"
    return f"{fence}{fence_lang(lang)}\n{code.rstrip()}\n{fence}"


def render(env: dict) -> str:
    tests, total = env["items"], len(env["edge_cases_covered"]) + len(env["edge_cases_not_covered"])
    framework = FRAMEWORK_NAMES.get(env["framework"], env["framework"])
    head = (f"**{len(tests)} tests for {_names(env['callables'])}** ({framework}) — covers "
            f"{len(env['edge_cases_covered'])} of {total} edge cases.")
    lines = [head, _code_block(env["tests_code"], env["language"])]
    if env["edge_cases_covered"]:
        lines.append(f"**Edge cases covered:** {_listing(env['edge_cases_covered'])}")
    if env["edge_cases_not_covered"]:
        lines.append(f"**Not covered:** {_listing(env['edge_cases_not_covered'])} (ask for more edge cases if you "
                     "want them)")
    if env["reasoned_tests"]:
        lines.append(f"**Heads up:** {env['reasoned_tests']} test{'s' if env['reasoned_tests'] != 1 else ''} assert "
                     f"values I worked out from the code, marked `{REASONED_NOTE}`. If one fails, check both the "
                     "test and the code.")
    bug = env.get("suspected_bug")
    if bug:
        lines.append(f"**Possible bug:** a test for `{clean(bug['callable'])}` may fail because the code looks "
                     f"wrong at `{clean(bug['quote'])}`: {clean(bug['why'])}")
    lines += _footer(env)
    return "\n\n".join(lines)


def _footer(env: dict) -> list[str]:
    lines = []
    if env["mocked"]:
        lines.append(f"Mocked: {_mock_list(env['mocked'])}.")
    if env["not_mocked"]:
        lines.append(f"Not mocked by any test (check before running): {_mock_list(env['not_mocked'])}.")
    if env["placeholder_import"]:
        lines.append(f"The import uses a placeholder module name: {CHANGE_IMPORT}.")
    if env["dropped"]:
        shown = "; ".join(f"{clean(d['name'])[:80]} ({clean(d['reason'])})" for d in env["dropped"][:6])
        lines.append(f"Left out {len(env['dropped'])} item(s) that failed my checks: {shown}.")
    if env["claims_removed"]:
        lines.append(f"Removed {env['claims_removed']} edge-case claim(s) I couldn't verify in the test body.")
    if env["suspected_bugs_dropped"]:
        lines.append("Left out a suspected-bug note whose quote isn't in your code.")
    if env["over_cap"]:
        lines.append(f"{len(env['over_cap'])} more test(s) didn't fit the 40-test / 400-line cap.")
    if env["not_tested"]:
        lines.append(f"Not tested yet (12 per turn): {_names(env['not_tested'])}. Say \"next batch\" for those.")
    lines += [f"_{clean(n)}_" for n in env["notes"]]
    lines.append("_Nothing was run: copy the file, run it, and look at any failure before trusting it._")
    return lines


def _mock_list(items: list[str]) -> str:
    return ", ".join(f"`{clean(i.split(' in ')[0])}` in `{clean(i.split(' in ')[-1])}`" for i in items)


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"
