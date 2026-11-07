"""The verdict: code-computed facts go to one tool-less model call (plus one JSON repair); the model's
answer is checked and overridden in code.

- `culprit_sha` must be code's culprit (or a prefix of it), else code's culprit (or null) is used;
- `log_quote` must appear verbatim in the raw log, else code's first anchor line is used;
- `suggested_fix` survives only for a compile or test-assertion failure, at a failure location inside a
  hunk of the culprit, in at most 200 characters;
- the summary may not name a sha, path, number or link absent from the facts, else a template is used.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

import classify
import excerpt
import history as h
import prompts
from analysis import Analysis
from culprit import in_hunk, same_path
from deps import TurnDeps
from reader import ask_once
from steps import load_object

log = logging.getLogger("ci_detective.verdict")

MAX_FIX_CHARS = 200
_TOKENS = re.compile(r"https?://\S+|\b[0-9a-f]{7,40}\b|[\w.-]*/[\w./-]+|\b[\w-]+\.[a-z]{1,5}\b|\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Fix:
    file: str
    line: int
    change: str


@dataclass(frozen=True)
class Verdict:
    kind: str          # real | flaky | infra | unclear
    summary: str
    culprit_sha: str
    log_quote: str
    fix: Fix | None
    failure_class: str


def facts(a: Analysis) -> str:
    """Everything the model may restate, as plain lines."""
    kind = h.verdict_kind(a.failure_class, a.flaky) if a.flaky else "unclear"
    top = a.narrowed.culprit if a.narrowed else None
    lines = [f"repository: {a.target.repo_full}", f"workflow: {a.run.workflow}", f"job: {a.job.name}",
             f"run: {a.run.run_id}", f"commit: {a.run.head_sha}", f"failure class: {a.failure_class}",
             f"failing tests: {', '.join(a.test_ids) or 'unknown'}",
             f"failure locations: {', '.join(str(x) for x in a.locations) or 'none'}", f"verdict: {kind}"]
    if a.flaky:
        lines.append(f"history: the job {h.rate_sentence(a.flaky)}")
    if top is not None:
        commit = top.commit
        lines += [f"culprit: {commit.sha} \"{commit.subject}\" by {commit.author}", *[f"evidence: {e}" for e in top.evidence]]
    elif a.narrowed and a.narrowed.note:
        lines.append(f"culprit: none ({a.narrowed.note})")
    return "\n".join(lines) + "\n\nlog excerpt:\n" + a.excerpt


async def decide(deps: TurnDeps, a: Analysis) -> Verdict:
    text = facts(a)
    messages = ({"role": "system", "content": prompts.VERDICT_SYSTEM},
                {"role": "user", "content": prompts.fence_facts(text)})
    answer = load_object(await ask_once(deps, messages))
    if answer is None:
        log.info("verdict answer was not JSON; asking once for a rewrite")
        repair = (*messages, {"role": "user", "content": "Reply with ONLY the JSON object described."})
        answer = load_object(await ask_once(deps, repair)) or {}
    return check(answer, a, text)


def check(answer: dict, a: Analysis, fact_text: str) -> Verdict:
    failure_class = a.failure_class
    if failure_class == "unknown":
        failure_class = classify.accept_model_class(answer.get("class"), answer.get("class_quote"), a.log_raw)
    flaky = a.flaky or h.Flakiness(n=1, k=1, u=0)
    kind = h.verdict_kind(failure_class, flaky)
    top = a.narrowed.culprit if a.narrowed else None
    code_sha = top.commit.sha if top is not None and kind != "infra" else ""
    return Verdict(kind=kind, summary=_summary(answer.get("summary"), fact_text, kind),
                   culprit_sha=_culprit(answer.get("culprit_sha"), code_sha), log_quote=_quote(answer.get("log_quote"), a),
                   fix=_fix(answer.get("suggested_fix"), a, failure_class, top), failure_class=failure_class)


def _culprit(proposed: object, code_sha: str) -> str:
    text = str(proposed or "").strip()
    if text and code_sha and len(text) >= 7 and code_sha.startswith(text):
        return code_sha
    return code_sha  # anything else is replaced by code's culprit (or none)


def _quote(proposed: object, a: Analysis) -> str:
    text = str(proposed or "").strip()
    if text and text in a.log_raw:
        return text
    return excerpt.first_anchor_line(a.log_raw) or a.class_line


def _fix(proposed: object, a: Analysis, failure_class: str, top) -> Fix | None:
    if not isinstance(proposed, dict) or failure_class not in ("compile", "test_assertion") or top is None:
        return None
    path, change = str(proposed.get("file") or ""), str(proposed.get("change") or "").strip()
    try:
        line = int(proposed.get("line"))
    except (TypeError, ValueError):
        return None
    at_failure = any(same_path(loc.path, path) and loc.line == line for loc in a.locations)
    if not change or len(change) > MAX_FIX_CHARS or not at_failure or not in_hunk(top.commit, path, line):
        return None
    return Fix(path, line, change)


def _summary(proposed: object, fact_text: str, kind: str) -> str:
    text = str(proposed or "").strip()
    if text and all(token in fact_text for token in _TOKENS.findall(text)):
        return text
    if text:
        log.info("summary named something absent from the facts; using the template")
    return template_summary(kind)


def template_summary(kind: str) -> str:
    return {
        "real": "The failing test started failing with the culprit commit below; earlier runs were green.",
        "flaky": "This test fails intermittently on the same code, so the failure is likely flaky.",
        "infra": "The job failed because of the CI environment, not the code.",
    }.get(kind, "There isn't enough history to say whether this is a real break or a flaky test.")


def with_class(a: Analysis, v: Verdict) -> Analysis:
    return replace(a, failure_class=v.failure_class)
