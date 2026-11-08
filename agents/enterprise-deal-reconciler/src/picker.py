"""Pure: the three questions this agent may pause on (one per pause), and reading their answers.

- several documents match → "Which document is the contract?" single-select (<= 10 titles) + custom text
- several customers match → "Which customer is this contract for?" single-select (<= 10) + custom text
- no readable contract   → "Paste the contract terms" (free text; the one option is Cancel)

The pause carries what the next step needs (the parsed request; for the customer question also the checked
contract terms), so it resumes on any replica from the task's own metadata.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata

METADATA_KEY = "deal_reconciler_pause"
MAX_OPTIONS = 10
DOCUMENT_HEADER = "Which document is the contract?"
CUSTOMER_HEADER = "Which customer is this contract for?"
CANCEL = "Cancel"
CANCELLED = "OK, I stopped. Ask again whenever you want to reconcile a deal."


@dataclass(frozen=True)
class Pause:
    text: str
    hitl: dict
    metadata: dict


def document_question(candidates: Sequence[Mapping[str, str]], request: dict) -> Pause:
    options = _labelled([(c["title"], c["id"]) for c in candidates[:MAX_OPTIONS]])
    hitl = question_metadata(DOCUMENT_HEADER, [(label, "") for label in options], allow_custom_input=True)
    text = f"Several documents match. {DOCUMENT_HEADER} Pick one, or type its exact title."
    return Pause(text, hitl, {METADATA_KEY: {"kind": "document", "request": request, "options": options}})


def customer_question(candidates: Sequence[Mapping[str, str | None]], request: dict, terms: dict,
                      meta: dict) -> Pause:
    rows = [(" ".join(p for p in (c.get("name") or "(no name)", f"<{c['email']}>" if c.get("email") else "") if p),
             str(c["id"])) for c in candidates[:MAX_OPTIONS]]
    options = _labelled(rows)
    hitl = question_metadata(CUSTOMER_HEADER, [(label, "") for label in options], allow_custom_input=True)
    text = f"Several customers match in billing. {CUSTOMER_HEADER} Pick one, or type the exact name or email."
    return Pause(text, hitl, {METADATA_KEY: {"kind": "customer", "request": request, "options": options,
                                             "terms": terms, "meta": meta}})


def paste_question(text: str, request: dict, header: str) -> Pause:
    hitl = question_metadata(header, [(CANCEL, "Stop here")], allow_custom_input=True)
    return Pause(text, hitl, {METADATA_KEY: {"kind": "paste", "request": request, "options": {}}})


def _labelled(rows: Sequence[tuple[str, str]]) -> dict[str, str]:
    """label → id; labels clipped to the platform's byte limit and made unique with the id."""
    options: dict[str, str] = {}
    for label, ident in rows:
        clean = _clip(" ".join(label.split()) or ident)
        if clean in options:
            clean = _clip(f"{clean} ({ident})")
        if clean not in options:
            options = {**options, clean: ident}
    return options


def _clip(label: str) -> str:
    return label.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def read_choice(answer: str, options: Mapping[str, str]) -> tuple[str | None, str]:
    """(the chosen id, or None; the typed text when no option matched)."""
    text = (answer or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    if first in options:
        return options[first], ""
    folded = {k.casefold(): v for k, v in options.items()}
    return folded.get(first.casefold()), "" if first.casefold() in folded else text


def is_cancel(answer: str) -> bool:
    return (answer or "").strip().casefold() in {"cancel", "skip", "stop", "no"}
