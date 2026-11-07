"""The model steps: one explain call over the computed facts (regex/IAM/nginx), at most one repair call,
the scope screen for a message with no config, and a follow-up answer. Every result is checked in code."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import checks
import prompts
from analysis import Analysis
from gotchas import clean_notes
from inputs import fence_text
from llm import Budget, TurnDeps, ask_json
from regex_check import verify_examples

MAX_FACTS_CHARS = 12_000


@dataclass(frozen=True)
class Explained:
    status: str                                 # ok | off_topic | failed
    explanation: str = ""
    notes: dict = field(default_factory=dict)
    examples: dict | None = None
    dropped_examples: int = 0
    removed_sentences: int = 0
    repaired: bool = False
    filled: tuple[str, ...] = ()                # items the model never named, filled in by code


def _facts_block(analysis: Analysis) -> str:
    text = json.dumps(analysis.facts, ensure_ascii=False, indent=1)
    return text if len(text) <= MAX_FACTS_CHARS else text[:MAX_FACTS_CHARS] + "\n… (facts truncated)"


def _user_message(analysis: Analysis, words: str) -> str:
    gotchas = "\n".join(f"- {g.id} ({g.severity}): {g.text}" for g in analysis.gotchas) or "- none"
    checked = "\n".join(f"- {line}" for line in analysis.checked) or "- nothing asked"
    return (f"USER REQUEST:\n{fence_text(words or 'Explain this.')}\n\nCONFIG ({analysis.kind}):\n"
            f"{fence_text(analysis.config)}\n\nFACTS:\n{_facts_block(analysis)}\n\nCHECKED BY CODE:\n{checked}\n\n"
            f"GOTCHAS:\n{gotchas}")


async def explain(deps: TurnDeps, budget: Budget, analysis: Analysis, words: str, org: tuple[str, ...]) -> Explained:
    messages = [{"role": "system", "content": prompts.explain_system(analysis.kind, org)},
                {"role": "user", "content": _user_message(analysis, words)}]
    data = await ask_json(deps, budget, messages)
    if data is None:
        return Explained("failed")
    if data.get("status") == "off_topic":
        return Explained("off_topic")
    text = str(data.get("explanation") or "").strip()
    repaired = False
    bad, missing = checks.problems(text, analysis)
    if (bad or missing or not text) and budget.can_call():
        fixed = await _repair(deps, budget, messages, data, bad, missing)
        if fixed is not None:
            data, text, repaired = fixed, str(fixed.get("explanation") or "").strip(), True
            bad, missing = checks.problems(text, analysis)
    if not text:
        return Explained("failed")
    text, removed = checks.drop_ungrounded(text, analysis)
    text = checks.no_judgements(text)
    _, missing = checks.problems(text, analysis)
    if missing:
        text += "\n\n" + "\n".join(fallback_line(analysis, item) for item in missing)
    examples, dropped = (verify_examples(analysis, data.get("examples")) if analysis.kind == "regex" else (None, 0))
    notes = clean_notes(data.get("notes"), [g.id for g in analysis.gotchas])
    notes = {k: checks.no_judgements(v) for k, v in notes.items()}
    return Explained("ok", text, notes, examples, dropped, removed, repaired, tuple(missing))


async def _repair(deps, budget, messages, data, bad, missing) -> dict | None:
    issues = [f"- not in the config or facts: `{span}`" for span in bad]
    issues += [f"- not mentioned: {item}" for item in missing] or []
    if not issues:
        issues = ["- the explanation was empty"]
    repair = [*messages, {"role": "assistant", "content": json.dumps(data)},
              {"role": "user", "content": prompts.REPAIR + "\n\nPROBLEMS:\n" + "\n".join(issues)}]
    fixed = await ask_json(deps, budget, repair)
    return fixed if fixed and fixed.get("status") == "ok" else None


def fallback_line(analysis: Analysis, item: str) -> str:
    """A code-built line for a statement or location the model didn't name."""
    if analysis.kind == "iam":
        for statement in analysis.facts.get("statements", []):
            if statement["statement"].startswith(item):
                actions = ", ".join(f"`{a}`" for a in statement["actions"] or statement["not_actions"]) or "no actions"
                resources = ", ".join(f"`{r['arn']}`" for r in statement["resources"]) or "the attached resource"
                return f"- **{statement['statement']}**: {statement['effect']} {actions} on {resources}."
    for server in analysis.facts.get("servers", []):
        for location in server["locations"]:
            if location["path"] == item:
                body = "; ".join(f"`{d}`" for d in location["directives"]) or "nested locations only"
                return f"- Location `{item}` (line {location['line']}): {body}."
    return f"- `{item}`"


async def screen(deps: TurnDeps, budget: Budget, words: str, org: tuple[str, ...]) -> str:
    """For a message with no config: "off_topic" or "needs_config"."""
    system = prompts.SCOPE_CHECK + ("\n\nOrganization instructions:\n" + "\n\n".join(org) if org else "")
    data = await ask_json(deps, budget, [{"role": "system", "content": system},
                                         {"role": "user", "content": fence_text(words)}])
    return "needs_config" if data and data.get("status") == "needs_config" else "off_topic"


async def follow_up(deps: TurnDeps, budget: Budget, analysis: Analysis, question: str,
                    org: tuple[str, ...]) -> tuple[str, str]:
    """(status, answer) for a question about the stored config; status is ok | off_topic | failed."""
    system = "\n\n".join([prompts.FOLLOWUP, prompts.SCOPE, prompts.DATA_RULES, *(["Organization instructions:\n"
                                                                                 + "\n\n".join(org)] if org else [])])
    data = await ask_json(deps, budget, [{"role": "system", "content": system},
                                         {"role": "user", "content": _user_message(analysis, question)}])
    if data and data.get("status") == "off_topic":
        return "off_topic", ""
    if not data or data.get("status") != "ok" or not str(data.get("answer") or "").strip():
        return "failed", ""
    answer, _ = checks.drop_ungrounded(str(data["answer"]), analysis)
    answer = checks.no_judgements(answer)
    return ("ok", answer) if answer else ("failed", "")
