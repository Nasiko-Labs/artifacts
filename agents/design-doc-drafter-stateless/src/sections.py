"""Pure: the technical writer's answer, cleaned per group (A: context + flow narrative; B: failure modes,
security, observability, rollout; C: acceptance criteria + the agent appendix; or the ADR). A group whose
required text is missing or only echoes the contract's hint is left out. The model writes section bodies
only; headings and order are code (assemble.py)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import prompts
from design import _dicts, _ids, _s, _strs
from grounding import normalise
from llm import off_topic

TEXT_KEYS = ("context", "flow_narrative", "security", "observability", "rollout", "adr_context", "adr_decision")
SECTION_KEYS = (*TEXT_KEYS, "failure_modes", "acceptance_criteria", "agent_appendix", "adr_consequences")
GROUPS = {"A": ("context", "flow_narrative"), "B": ("failure_modes", "security", "observability", "rollout"),
          "C": ("acceptance_criteria", "agent_appendix"), "ADR": ("adr_context", "adr_decision", "adr_consequences")}
STEP_KINDS = ("code", "single_call", "agent_loop")
FM_FIELDS = ("failure", "effect", "detection", "mitigation")


_ECHOES = frozenset(normalise(h) for h in prompts.HINTS.values())


def _text(value: Any) -> str:
    """The field's text, or "" when the model only echoed the contract's hint or a placeholder."""
    text = _s(value)
    bare = normalise(text).strip("<>. ")
    return "" if not bare or bare in _ECHOES or text.startswith("<") or bare in prompts.HINTS else text


def clean_criteria(value: Any, ids: Sequence[str]) -> list[dict]:
    out = []
    for c in _dicts(value):
        crit = {"given": _s(c.get("given")), "when": _s(c.get("when")), "then": _s(c.get("then")),
                "verifies": _ids(c.get("verifies"), ids)}
        if crit["then"] and crit["verifies"]:
            out.append(crit)
    return out


def clean_appendix(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    steps = [{"step": _s(s.get("step")), "kind": _s(s.get("kind")) if _s(s.get("kind")) in STEP_KINDS else "code",
              "reason": _s(s.get("reason"))} for s in _dicts(value.get("steps")) if _s(s.get("step"))]
    return {"steps": steps, **{k: _text(value.get(k)) for k in ("approvals", "guardrails", "evaluation", "budget")}}


def clean(data: dict, ids: Sequence[str]) -> dict:
    """Every section key present in `data`, cleaned; absent keys are left out."""
    out: dict[str, Any] = {k: _text(data[k]) for k in TEXT_KEYS if k in data}
    if "failure_modes" in data:
        out["failure_modes"] = [{f: _s(m.get(f)) for f in FM_FIELDS} for m in _dicts(data["failure_modes"])
                                if _s(m.get("failure"))]
    if "acceptance_criteria" in data:
        out["acceptance_criteria"] = clean_criteria(data["acceptance_criteria"], ids)
    if "agent_appendix" in data:
        out["agent_appendix"] = clean_appendix(data["agent_appendix"])
    if "adr_consequences" in data:
        out["adr_consequences"] = _strs(data["adr_consequences"])
    return out


def validator(key: str, ids: Sequence[str]):
    def validate(data: Any) -> dict | None:
        if off_topic(data):
            return {"status": "off_topic"}
        if not isinstance(data, dict):
            return None
        if key == "ADR":
            data = {f"adr_{k}": data[k] for k in ("context", "decision", "consequences") if k in data}
        cleaned = clean(data, ids)
        required = [k for k in GROUPS[key] if k in TEXT_KEYS] or [GROUPS[key][0]]
        if not all(cleaned.get(k) for k in required):
            return None
        return {"status": "ok", **cleaned}
    return validate


def read(data: Any, keys: Sequence[str], ids: Sequence[str]) -> tuple[str, dict, int]:
    """(status, the valid groups' sections merged, how many groups were missing or invalid)."""
    if off_topic(data):
        return "off_topic", {}, 0
    answers = [validator(key, ids)(data) for key in keys]
    merged: dict[str, Any] = {}
    for key, answer in zip(keys, answers):
        merged.update({k: v for k, v in (answer or {}).items() if k in GROUPS[key]})
    return "ok", merged, sum(1 for a in answers if a is None)
