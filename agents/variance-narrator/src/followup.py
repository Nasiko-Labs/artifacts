"""Pure: follow-ups on a stored table. A short command ("board version", "only unfavourable", "threshold 3%",
"table only", "as json", "explain again") is parsed in code; "add driver: ..." is bound to one line in code;
anything else is a question for one scoped call."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import drivers
import request

COMMAND_MAX_WORDS = 12
_TABLE_ONLY = re.compile(r"\b(?:table only|just the table|only the table|the table)\b", re.IGNORECASE)
_RENARRATE = re.compile(r"\b(?:explain|narrate|write) (?:it |this |that )?again\b|\bre-?narrate\b|\brewrite\b|"
                        r"\bregenerate\b|\bre-?write\b", re.IGNORECASE)
_ADD_DRIVER = re.compile(r"^\s*(?:please\s+)?add (?:a |the )?(?:driver|reason|explanation)\s*(?:for\s+)?:?\s*(.+)$",
                         re.IGNORECASE | re.DOTALL)
_KNOWN = re.compile(r"\b(?:shorter|short|brief(?:er)?|one|two|1|2|sentences?|tl|dr|board|full|default|longer|normal|"
                    r"standard|version|table|only|just|unfavou?rable|favou?rable|lines?|ones|items|variances?|all|"
                    r"show|every|threshold|flag|materiality|over|above|of|to|at|as|json|in|explain|narrate|write|"
                    r"rewrite|re|regenerate|again|it|this|that|please|pls|can|could|you|give|me|make|the|a|an|now|"
                    r"and|with|use|set|instead|narrative|paragraph|explanation|ok|okay|then|for|version|ready|"
                    r"\d+(?:\.\d+)?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    settings: request.Settings
    table_only: bool = False
    renarrate: bool = False


@dataclass(frozen=True)
class DriverAdd:
    name: str = ""
    text: str = ""
    candidates: tuple[str, ...] = ()   # more than one line matched


def parse_command(text: str) -> Command | None:
    """A re-render command, or None when the message says anything else."""
    plain = " ".join((text or "").replace("%", " ").replace(";", " ").replace("-", " ").split())
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS:
        return None
    if " ".join(_KNOWN.sub(" ", re.sub(r"[^\w\s.]", " ", plain)).replace(".", " ").split()):
        return None
    settings = request.settings_of(text)
    command = Command(settings, bool(_TABLE_ONLY.search(text)), bool(_RENARRATE.search(text)))
    changed = (settings.length, settings.threshold, settings.only)
    if all(v is None for v in changed) and not (command.table_only or command.renarrate or settings.wants_json):
        return None
    return command


def parse_add_driver(text: str, names: Sequence[str]) -> DriverAdd | None:
    """None when the message is not an "add driver" request."""
    match = _ADD_DRIVER.match(text or "")
    if not match:
        return None
    body = drivers.clean(match.group(1))
    head, colon, rest = body.partition(":")
    if colon and rest.strip():
        exact = [n for n in names if n.casefold() == head.strip().casefold()]
        if len(exact) == 1:
            return DriverAdd(exact[0], drivers.clean(rest))
    found = drivers.mentions(body, names)
    if len(found) == 1:
        return DriverAdd(found[0], body)
    shared = drivers.sharing(body, names) if not found else found
    return DriverAdd(text=body, candidates=tuple(shared) if len(shared) > 1 else ())
