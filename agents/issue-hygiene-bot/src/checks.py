"""The code post-checks: the guarantee, not the prompt.

Every id, link, title, name, email and date the model reports must appear in this turn's tool output.
Ids are matched whole; text after normalising case, whitespace and JSON escapes. An issue whose id is
unseen is dropped; an unseen link, title, name or date is blanked (shown as unknown), never kept."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from config import Config
from dates import parse_day
from records import ChatUser, Issue

UNVERIFIED = "unverified"
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a text says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str, seen: str) -> bool:
    """`needle` occurs in already-normalised tool output. Empty never matches."""
    wanted = normalise_text(needle).strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def id_seen(identifier: str, seen: str) -> bool:
    """A whole id (`PLAT-120`, `U024BE7LH`) in normalised tool output: not part of a longer id."""
    wanted = normalise_text(identifier)
    if not wanted:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])", seen) is not None


@dataclass(frozen=True)
class Scope:
    team: str = ""
    project: str = ""
    issue_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IssueCheck:
    kept: tuple[Issue, ...]
    dropped: int = 0          # not backed by tool output, or a duplicate
    out_of_scope: int = 0     # another team/project than asked
    capped: int = 0           # over the issue cap
    teams_capped: tuple[str, ...] = ()


def check_issues(issues: Iterable[Issue], seen_text: str, scope: Scope, config: Config) -> IssueCheck:
    seen = normalise_text(seen_text)
    raw = list(issues)
    unique: dict[str, Issue] = {}
    for issue in raw:
        if issue.id.casefold() in unique or not id_seen(issue.id, seen):
            continue
        unique[issue.id.casefold()] = _verified(issue, seen)
    in_scope = [i for i in unique.values() if _in_scope(i, scope)]
    inside, teams_capped = _cap_teams(in_scope, scope, config.max_teams)
    kept = tuple(inside[:config.max_issues])
    return IssueCheck(kept, dropped=len(raw) - len(unique), out_of_scope=len(unique) - len(in_scope),
                      capped=len(in_scope) - len(kept), teams_capped=teams_capped)


def _verified(issue: Issue, seen: str) -> Issue:
    def kept(value: str) -> str:
        return value if appears_in(value, seen) else ""

    def optional(value: str | None) -> str | None:
        return None if value is None else (value if appears_in(value, seen) else UNVERIFIED)

    return replace(issue, url=kept(issue.url), title=kept(issue.title) or issue.id, state=kept(issue.state),
                   assignee_name=kept(issue.assignee_name), assignee_email=kept(issue.assignee_email),
                   project=optional(issue.project), estimate=optional(issue.estimate), priority=kept(issue.priority),
                   created_at=_date(issue.created_at, seen), updated_at=_date(issue.updated_at, seen),
                   team=kept(issue.team))


def _date(value: str, seen: str) -> str:
    """A date is kept only when it parses and the tool returned it (the full value or its day)."""
    if parse_day(value) is None:
        return ""
    return value if appears_in(value, seen) or appears_in(value[:10], seen) else ""


def _in_scope(issue: Issue, scope: Scope) -> bool:
    if scope.issue_ids and issue.id.casefold() not in {i.casefold() for i in scope.issue_ids}:
        return False
    if scope.team and issue.team and not _team_matches(issue, scope.team):
        return False
    if scope.project and issue.project not in (None, UNVERIFIED):
        return scope.project.casefold() in issue.project.casefold()
    return True


def _team_matches(issue: Issue, wanted: str) -> bool:
    folded = wanted.casefold()
    return folded == issue.team.casefold() or issue.id.casefold().startswith(f"{folded}-")


def _cap_teams(issues: list[Issue], scope: Scope, max_teams: int) -> tuple[list[Issue], tuple[str, ...]]:
    if scope.team:
        return issues, ()
    teams = list(dict.fromkeys(i.team for i in issues if i.team))
    if len(teams) <= max_teams:
        return issues, ()
    allowed = set(teams[:max_teams])
    return [i for i in issues if not i.team or i.team in allowed], tuple(teams[max_teams:])


def check_users(users: Iterable[ChatUser], seen_text: str, wanted: Sequence[str]) -> tuple[ChatUser, ...]:
    """A chat user counts only when the email is one we asked about and both the email and the user id came
    back from the chat tool. The name is blanked when unseen."""
    seen = normalise_text(seen_text)
    asked = {e.casefold() for e in wanted}
    kept = [replace(u, name=u.name if appears_in(u.name, seen) else "") for u in users
            if u.email.casefold() in asked and appears_in(u.email, seen) and id_seen(u.user_id, seen)]
    return tuple({u.email.casefold(): u for u in kept}.values())
