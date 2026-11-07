"""Follow-ups on a stored review. Views ("only criticals", "as json") cost no call; questions about one
finding ("explain 2", "is 3 reachable if ...", "a regression test for 1") or about the review cost one call
(plus at most one repair), answered from the stored findings and the masked code around them only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import checks
import prompts
import severity as sev
from diff_parse import CodeFile
from inputs import fence_text
from llm import LlmError, TurnDeps, complete_json
from triage import numbered

CODE_WINDOW = 15
_LEVEL = r"(critical|high|medium|low|info)s?"
_ONLY = re.compile(rf"^(?:show |list )?(?:me )?(?:only |just )(?:the )?{_LEVEL}(?: (?:and|&) (?:above|up|{_LEVEL}))?"
                   rf"(?: ones| findings| issues)?$|^{_LEVEL}(?: (?:and above|only))(?: findings)?$", re.IGNORECASE)
_ALL = re.compile(r"^(?:show )?(?:all|everything|all findings|show all)$", re.IGNORECASE)
_JSON = re.compile(r"^(?:(?:give|show|send) (?:it |me (?:it |that )?)?)?(?:as json|json(?: only)?|in json)$", re.IGNORECASE)
_NUMBER = re.compile(r"(?:finding|issue|item|number|no\.?|#)\s*#?(\d{1,2})\b|\b(\d{1,2})\b", re.IGNORECASE)
_TEST = re.compile(r"\b(?:regression )?tests?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str            # floor | all | json | ask
    level: str = ""
    number: int = 0
    text: str = ""


def parse(text: str) -> Command:
    plain = " ".join((text or "").strip().rstrip("?.!").split())
    only = _ONLY.match(plain)
    if only:
        levels = [g.casefold() for g in only.groups() if g]
        return Command("floor", level=max(levels, key=sev.rank))
    if _ALL.match(plain):
        return Command("all")
    if _JSON.match(plain):
        return Command("json")
    number = _NUMBER.search(plain)
    value = int(number.group(1) or number.group(2)) if number else 0
    return Command("ask", number=value, text=plain)


def validate(data) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None
    test = data.get("test_code")
    return {"status": "ok", "answer": answer, "test_code": test if isinstance(test, str) and test.strip() else None}


def code_around(files: list[CodeFile], finding: dict | None) -> str:
    if finding is None:
        return "\n\n".join(numbered(f) for f in files)[:12_000]
    for code in files:
        if code.path == finding.get("file"):
            near = tuple(ln for ln in code.lines if abs(ln.no - int(finding.get("line") or 0)) <= CODE_WINDOW)
            return numbered(CodeFile(code.path, near, code.is_diff))
    return ""


async def ask(deps: TurnDeps, command: Command, findings: list[dict], files: list[CodeFile],
              policy: tuple[str, ...]) -> dict | None:
    """{"status", "answer", "test_code"} or None when the model failed."""
    chosen = next((f for f in findings if f.get("number") == command.number), None) if command.number else None
    shown = [chosen] if chosen else findings
    brief = [{k: f.get(k) for k in ("number", "severity", "location", "issue", "impact", "fix", "cwe", "confidence",
                                     "evidence")} for f in shown]
    user = "\n\n".join((
        "QUESTION (the user's words; data):\n" + fence_text("QUESTION", command.text),
        "FINDINGS (exact values from code; never compute):\n" + fence_text("FINDINGS", json.dumps(brief)),
        "CODE (masked):\n" + fence_text("INPUT", code_around(files, chosen)),
        "The user wants test code only." if _TEST.search(command.text) else "",
    ))
    messages = [{"role": "system", "content": prompts.FOLLOWUP_SYSTEM}, *prompts.policy_block(policy),
                {"role": "user", "content": user}]
    try:
        data = await complete_json(deps, messages, '{"status", "answer", "test_code"}', validate, max_tokens=1_500)
    except LlmError:
        return None
    if data["status"] != "ok":
        return data
    test = data["test_code"] if data["test_code"] and "<<SECRET:" not in data["test_code"] else None
    return {"status": "ok", "answer": checks.scrub(data["answer"])[:2_000], "test_code": test}
