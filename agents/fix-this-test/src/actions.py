"""Pure: the model's only way to act. Four typed JSON actions, validated in code before anything happens.
No shell, no pip, no network, no file creation, no deletes: there is no action for them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runner import SCOPES

MAX_READ_LINES = 200
MAX_NEW_CHARS = 4_000
MAX_EXPLANATION_CHARS = 1_200
_COMMON = {"thought", "action"}
_KEYS = {
    "read": {"path", "start", "end"},
    "edit": {"path", "old", "new", "target", "reason", "quote"},
    "run": {"scope"},
    "finish": {"explanation"},
}


@dataclass(frozen=True)
class Action:
    kind: str
    thought: str = ""
    path: str = ""
    start: int = 1
    end: int = MAX_READ_LINES
    old: str = ""
    new: str = ""
    target: str = "source"
    reason: str = ""
    quote: str = ""
    scope: str = ""
    explanation: str = ""


def parse(data: Any) -> Action | str:
    """An Action, or the error observation to send back."""
    if not isinstance(data, dict):
        return "error: answer with one JSON object"
    if data.get("status") == "off_topic":
        return Action("off_topic")
    kind = data.get("action")
    if kind not in _KEYS:
        return "error: action must be one of read, edit, run, finish (there is no shell or other action)"
    extra = set(data) - _COMMON - _KEYS[kind]
    if extra and kind == "run":
        return "error: run takes only \"scope\"; commands, arguments and paths are not allowed"
    thought = str(data.get("thought") or "")[:400]
    builder = {"read": _read, "edit": _edit, "run": _run, "finish": _finish}[kind]
    return builder(data, thought)


def _read(data: dict, thought: str) -> Action | str:
    path = data.get("path")
    if not isinstance(path, str) or not path:
        return "error: read needs a \"path\""
    start = _int(data.get("start"), 1)
    end = _int(data.get("end"), start + MAX_READ_LINES - 1)
    start = max(1, start)
    end = max(start, min(end, start + MAX_READ_LINES - 1))
    return Action("read", thought, path=path, start=start, end=end)


def _edit(data: dict, thought: str) -> Action | str:
    path, old, new = data.get("path"), data.get("old"), data.get("new")
    if not isinstance(path, str) or not isinstance(old, str) or not isinstance(new, str) or not old:
        return "error: edit needs \"path\", a non-empty \"old\" and \"new\""
    if len(new) > MAX_NEW_CHARS:
        return f"error: \"new\" is over {MAX_NEW_CHARS} characters; make a smaller change"
    if old == new:
        return "error: \"old\" and \"new\" are the same"
    target = "test" if data.get("target") == "test" else "source"
    return Action("edit", thought, path=path, old=old, new=new, target=target,
                  reason=str(data.get("reason") or ""), quote=str(data.get("quote") or ""))


def _run(data: dict, thought: str) -> Action | str:
    scope = data.get("scope")
    if scope not in SCOPES:
        return "error: run \"scope\" must be \"failing\", \"file\" or \"all\""
    return Action("run", thought, scope=scope)


def _finish(data: dict, thought: str) -> Action | str:
    return Action("finish", thought, explanation=str(data.get("explanation") or "")[:MAX_EXPLANATION_CHARS])


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
