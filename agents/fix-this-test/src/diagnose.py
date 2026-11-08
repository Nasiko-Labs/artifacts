"""The two no-edit model calls: likely causes when the test passes here (flaky / can't reproduce), and a
plain explanation (of a failure, or of a fix already made). Quotes are checked verbatim against the
pasted input; an unverified cause is dropped and counted."""

from __future__ import annotations

from dataclasses import dataclass

import prompts
from inputs import appears_in, fence_text
from llm import Budget, BudgetExhausted, TurnDeps, ask_json, system_with_org

MAX_CAUSES = 4
MAX_CODE_CHARS = 30_000
OFF_TOPIC = "off_topic"


@dataclass(frozen=True)
class Diagnosis:
    passes: int
    runs: int
    causes: tuple[tuple[str, str], ...] = ()   # (cause, verified quote)
    dropped: int = 0


def _code(files: dict[str, str]) -> str:
    return "\n".join(f"# file: {p}\n{t}" for p, t in files.items())[:MAX_CODE_CHARS]


async def flaky(deps: TurnDeps, budget: Budget, failure: str, files: dict[str, str], passes: int, runs: int,
                org: tuple[str, ...] = ()) -> Diagnosis:
    evidence = failure + "\n" + "\n".join(files.values())
    user = "\n".join([f"Here the failing test passed {passes} of {runs} re-runs (computed).",
                      "The user's failure output:", fence_text(failure[:8_000]),
                      "The pasted files:", fence_text(_code(files))])
    try:
        data = await ask_json(deps, budget, [{"role": "system", "content": system_with_org(prompts.FLAKY_SYSTEM, org)},
                                             {"role": "user", "content": user}])
    except BudgetExhausted:
        data = None
    raw = (data or {}).get("causes") if isinstance(data, dict) else None
    causes, dropped = [], 0
    for item in (raw if isinstance(raw, list) else [])[:MAX_CAUSES]:
        text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
        quote = str(item.get("quote") or "").strip() if isinstance(item, dict) else ""
        if text and quote and appears_in(quote, evidence):
            causes.append((text, quote))
        else:
            dropped += 1
    return Diagnosis(passes, runs, tuple(causes), dropped)


async def explain(deps: TurnDeps, budget: Budget, question: str, facts: list[str], failure: str,
                  files: dict[str, str], org: tuple[str, ...] = ()) -> str | None:
    """The model's explanation (unchecked; checks.py checks it), OFF_TOPIC, or None when it gave none."""
    user = "\n".join([f"The user's message: {fence_text(question)}", *facts,
                      "Failure output:", fence_text(failure[:8_000]), "Files:", fence_text(_code(files))])
    try:
        data = await ask_json(deps, budget, [{"role": "system",
                                              "content": system_with_org(prompts.EXPLAIN_SYSTEM, org)},
                                             {"role": "user", "content": user}])
    except BudgetExhausted:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return OFF_TOPIC
    text = data.get("explanation")
    return text.strip() if isinstance(text, str) and text.strip() else None


async def classify(deps: TurnDeps, budget: Budget, text: str, org: tuple[str, ...] = ()) -> str:
    """One label for a message with no pasted output or files (see INTAKE_SYSTEM)."""
    data = await ask_json(deps, budget, [{"role": "system", "content": system_with_org(prompts.INTAKE_SYSTEM, org)},
                                         {"role": "user", "content": fence_text(text[:4_000])}])
    status = (data or {}).get("status") if isinstance(data, dict) else None
    return status if status in ("fix_request", "explain", "retry", "unsupported_language") else OFF_TOPIC
