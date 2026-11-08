"""Pure: applying `read` and `edit` actions to the run's in-memory copy of the files. An edit's `old` must
occur exactly once; a `.py` file that no longer compiles is reverted and the error is the observation."""

from __future__ import annotations

from dataclasses import dataclass

from actions import Action

MAX_OBSERVATION_CHARS = 3_000


@dataclass(frozen=True)
class Applied:
    text: str | None          # the file's new text, or None when the edit was not applied
    observation: str


def read(files: dict[str, str], action: Action) -> str:
    if action.path not in files:
        return f"error: {action.path} is not one of the pasted files: {', '.join(files)}"
    lines = files[action.path].splitlines()
    end = min(action.end, len(lines))
    numbered = [f"{n:>4} | {lines[n - 1]}" for n in range(action.start, end + 1)]
    head = f"{action.path} lines {action.start}-{end} of {len(lines)}:"
    return "\n".join([head, *numbered])[:MAX_OBSERVATION_CHARS]


def apply(files: dict[str, str], action: Action) -> Applied:
    text = files.get(action.path)
    if text is None:
        return Applied(None, f"error: {action.path} is not one of the pasted files; new files are not allowed")
    count = text.count(action.old)
    if count == 0 and action.new in text:
        return Applied(None, f"error: this change is already in {action.path}; do not repeat it. Next: "
                             "{\"action\": \"run\", \"scope\": \"all\"}, then finish")
    if count == 0:
        return Applied(None, "error: 0 matches for \"old\"; read the file and copy the text exactly")
    if count > 1:
        return Applied(None, f"error: {count} matches for \"old\"; add surrounding lines so it is unique")
    updated = text.replace(action.old, action.new, 1)
    if action.path.endswith(".py"):
        try:
            compile(updated, action.path, "exec")
        except SyntaxError as e:
            return Applied(None, f"error: the edit broke the syntax of {action.path} (line {e.lineno}: {e.msg}); "
                                 "reverted")
    return Applied(updated, f"edited {action.path}")
