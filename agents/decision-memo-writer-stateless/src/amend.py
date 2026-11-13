"""Pure: the changes a follow-up makes to a brief, in code. Used both to replay the changes found in history
(this agent keeps no state) and to apply this turn's change, so a memo rebuilt from history matches the one
the user last saw in substance.

    "recommend B instead"            -> override (kept by "add data", dropped by a new option or new weights)
    "add option C: ..."              -> one more option and its text as data; a fresh comparison
    "add data: ..."                  -> one more line of grounding data
    "change the weights: cost 60%"   -> criteria reweighted in code; the criteria decide again
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import brief as b
import criteria as crit
import followup as fu
import options as opts

KINDS = ("override", "add_option", "add_data", "weights")


@dataclass(frozen=True)
class Amended:
    brief: b.Brief
    override: str = ""
    error: str = ""                   # why this change could not be applied ("" when it was)


def replay(brief: b.Brief, commands: Sequence[fu.Command]) -> Amended:
    """Every earlier change in order; one that could not be applied then is skipped now too."""
    state = Amended(brief)
    for command in commands:
        step = apply(state, command)
        state = state if step.error else step
    return state


def apply(state: Amended, command: fu.Command) -> Amended:
    brief = state.brief
    if command.kind == "override":
        key = opts.match(command.text, brief.options)
        if not key:
            return Amended(brief, state.override, f'There\'s no option "{command.text}" in this memo. '
                                                  f"{option_list(brief)}")
        return Amended(brief, key)
    if command.kind == "add_option":
        return _add_option(state, command.text)
    if command.kind == "add_data":
        return Amended(b.replace(brief, extra=(*brief.extra, command.text)), state.override)
    if command.kind == "weights":
        return _reweight(state, command.text)
    return state


def _add_option(state: Amended, text: str) -> Amended:
    brief = state.brief
    if len(brief.options) >= opts.MAX_OPTIONS:
        return Amended(brief, state.override, f"This memo already compares {opts.MAX_OPTIONS} options, the most one "
                                              "memo holds. Start a new brief with the options you want.")
    added = opts.make([text])[0]
    option = opts.Option(opts.next_key(brief.options), added.name, added.text)
    grown = b.replace(brief, options=(*brief.options, option), extra=(*brief.extra, f"Option {option.key}: {text}"))
    return Amended(grown)  # a fresh comparison: an earlier override no longer applies


def _reweight(state: Amended, text: str) -> Amended:
    brief = state.brief
    if not brief.criteria:
        return Amended(brief, state.override, "This memo has no criteria to reweight. Send a new brief with "
                                              "criteria, for example \"Criteria: cost 40%, speed 35%, risk 25%\".")
    changes, unknown = fu.weight_changes(text, brief.criteria)
    if unknown or not changes or sum(changes.values()) > 100:
        names = ", ".join(c.name for c in brief.criteria)
        return Amended(brief, state.override,
                       f"I couldn't apply those weights. The criteria are: {names}. Say, for example, \"change the "
                       f"weights: {brief.criteria[0].name} 60%\" (new weights add up to 100% at most).")
    return Amended(b.replace(brief, criteria=crit.reweight(brief.criteria, changes)))  # the criteria decide again


def option_list(brief: b.Brief) -> str:
    return "The options are " + "; ".join(f"{o.key} ({o.name})" for o in brief.options) + "."
