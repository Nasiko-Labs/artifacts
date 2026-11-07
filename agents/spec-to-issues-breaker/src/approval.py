"""The questions this agent pauses on (one per pause) and reading their answers, in code.

- the team question (single-select of team names);
- the picker: a multi-select over the proposed issues plus Cancel; a selection, never an approval;
- one card per write: Create / Skip / Cancel, custom text = an edit of the title or description.
The platform returns a single-select as its label, a multi-select as the picked labels one per line followed
by any typed text, and free text verbatim."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from records import ProposedIssue, Team

TEAM_HEADER = "Which team should the issues go to?"
PICK_HEADER = "Which issues should I create?"
CREATE, SKIP, CANCEL = "Create", "Skip", "Cancel"
_TITLE_EDIT = re.compile(r"^\s*title\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_DESCRIPTION_EDIT = re.compile(r"^\s*description\s*:\s*(.+)", re.IGNORECASE | re.DOTALL | re.MULTILINE)


def _fit(label: str) -> str:
    raw = label.encode("utf-8")
    if len(raw) <= MAX_OPTION_LABEL_LEN:
        return label
    return raw[:MAX_OPTION_LABEL_LEN - 3].decode("utf-8", errors="ignore").rstrip() + "..."


def team_question(teams: Sequence[Team], most: int) -> tuple[dict, list[str]]:
    names = list(dict.fromkeys(t.name for t in teams))[:most]
    return question_metadata(TEAM_HEADER, [(n, "") for n in names], allow_custom_input=False), names


def chosen_team(answer: str, teams: Sequence[Team]) -> Team | None:
    wanted = answer.strip().splitlines()[0].strip().casefold() if answer.strip() else ""
    return next((t for t in teams if wanted in (t.name.casefold(), t.key.casefold())), None)


def picker(issues: Sequence[ProposedIssue], allow_edit: bool) -> tuple[dict, dict[str, int]]:
    offered: dict[str, int] = {}
    options = []
    for issue in issues:
        label = _fit(f"#{issue.local_id} {issue.title}" + (f" (exists: {issue.exists})" if issue.exists else ""))
        if label in offered:
            continue
        offered[label] = issue.local_id
        detail = "Already in the tracker: stays skipped." if issue.exists else issue.description[:300]
        options.append((label, detail))
    options.append((CANCEL, "Create nothing."))
    return question_metadata(PICK_HEADER, options, multi_select=True, allow_custom_input=allow_edit), offered


@dataclass(frozen=True)
class Picked:
    ids: tuple[int, ...] = ()
    cancel: bool = False
    text: str = ""                # typed text: an edit instruction


def read_picker(answer: str, offered: Mapping[str, int]) -> Picked:
    ids: list[int] = []
    typed: list[str] = []
    cancel = False
    for line in (answer or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        if clean.casefold() == CANCEL.casefold():
            cancel = True
        elif clean in offered:
            ids.append(int(offered[clean]))
        else:
            typed.append(clean)
    return Picked(tuple(dict.fromkeys(ids)), cancel, "\n".join(typed))


def card_question(header: str, allow_edit: bool) -> dict:
    options = [(CREATE, "Make exactly this write."), (SKIP, "Do not create this one; go on."),
               (CANCEL, "Stop here; nothing more is created.")]
    return question_metadata(header, options, allow_custom_input=allow_edit)


@dataclass(frozen=True)
class CardAnswer:
    kind: str                     # create | skip | cancel | edit | unknown
    title: str = ""
    description: str = ""


def read_card(answer: str) -> CardAnswer:
    text = (answer or "").strip()
    first = text.splitlines()[0].strip().casefold() if text else ""
    for kind, word in (("create", CREATE), ("skip", SKIP), ("cancel", CANCEL)):
        if first == word.casefold():
            return CardAnswer(kind)
    title = _TITLE_EDIT.search(text)
    description = _DESCRIPTION_EDIT.search(text)
    if title or description:
        title_text = title.group(1).strip() if title else ""
        if description and title and title.start() > description.start():
            title_text = ""  # a "title:" inside the new description is part of it
        return CardAnswer("edit", title_text, description.group(1).strip() if description else "")
    return CardAnswer("unknown")
