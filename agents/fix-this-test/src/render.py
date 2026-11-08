"""Pure: the markdown reply (or the fenced JSON envelope when asked), built by code from the envelope.
Markdown only: no raw HTML, no images, no links that were not in the input."""

from __future__ import annotations

import json
import re

_TICKS = re.compile(r"`{3,}")


def fence(body: str, info: str = "") -> str:
    longest = max((len(m) for m in _TICKS.findall(body)), default=2)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{body.rstrip(chr(10))}\n{ticks}"


def as_json(envelope: dict) -> str:
    return fence(json.dumps(envelope, indent=2, ensure_ascii=False), "json")


def _counts(c: dict) -> str:
    return f"{c['failed']} failed, {c['passed']} passed" + (f", {c['errors']} errors" if c["errors"] else "")


def fix_reply(env: dict) -> str:
    after, before = env["tests_after"], env["tests_before"]
    total = after["failed"] + after["passed"] + after["errors"]
    targets = ", ".join(f"`{t}`" for t in env["targets"]) or "the failing test"
    mode = "ran in a sandbox" if env["mode"] == "sandbox" else "static (tests were not run)"
    if env["verified"]:
        lead = (f"**Fixed and verified**: {targets} now passes; {after['passed']} of {total} tests pass "
                f"(was {_counts(before)}). Mode: {mode}.")
    elif not env["patch"]:
        lead = f"**No fix found.** I couldn't produce a change for {targets}; the code is unchanged. Mode: {mode}."
    elif env["mode"] == "static":
        lead = (f"**Not verified.** I could not verify this fix ({env['static_reason']}). Treat it as a "
                "suggestion.")
    else:
        lead = (f"**Not verified.** After this change the re-run shows {_counts(after)} (was {_counts(before)}). "
                "Treat it as a suggestion.")
    parts = [lead]
    if env["explanation"]:
        parts.append(f"**What was wrong.** {env['explanation']}")
    if env["patch"]:
        parts.append("**Patch** (apply with `git apply` from the repository root when the paths are "
                     "repo-relative):\n" + fence(env["patch"], "diff"))
    parts.append("**Checked:** " + _checked(env))
    if env["warnings"]:
        parts.append("**Warnings:**\n" + "\n".join(f"- {w}" for w in env["warnings"]))
    parts.append(f"Confidence: {env['confidence']}. " + ("Please review before applying." if env["needs_human"]
                                                          else "Safe to apply after a quick look."))
    return "\n\n".join(parts)


def _checked(env: dict) -> str:
    changed = sum(i["added"] + i["removed"] for i in env["items"])
    bits = ["test file edited (see warnings)" if env["changed_test"] else "test file unchanged",
            "no assertions removed", f"{changed} lines changed"]
    if env.get("refused_edits"):
        bits.append(f"{env['refused_edits']} unsafe edit(s) refused")
    if env["verified"]:
        bits.append(f"verified by re-running: all {env['tests_after']['passed']} tests pass")
    return " · ".join(bits) + "."


def flaky_reply(env: dict) -> str:
    passes, runs = env["reruns_passed"], env["reruns"]
    if passes == runs:
        lead = (f"**I can't reproduce this: it passes {passes} of {runs} runs here.** No patch. Likely causes in "
                "your output and code:")
    else:
        lead = (f"**Flaky: it passed {passes} of {runs} runs here.** No patch. Here is the nondeterminism I can "
                "see:")
    causes = [f"- {i['cause']} (`{i['quote']}`)" for i in env["items"]] or ["- I couldn't point to a specific line."]
    parts = [lead, "\n".join(causes)]
    if env["warnings"]:
        parts.append("**Notes:**\n" + "\n".join(f"- {w}" for w in env["warnings"]))
    return "\n\n".join(parts)


def explain_reply(text: str, dropped: int) -> str:
    note = (f"\n\n_{dropped} sentence(s) were dropped: they named things not in your input._" if dropped else "")
    return f"**Why it fails / what changed.** {text or 'I could not give an explanation grounded in your files.'}" \
           f" No edits were made.{note}"
