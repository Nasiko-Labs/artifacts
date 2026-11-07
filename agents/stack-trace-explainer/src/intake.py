"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                                    -> canned intro (no call)
    a result stored ─┬─ a new trace pasted                   -> explain it (turn 1 again)
                     ├─ code pasted, no trace                -> attach to the stored primary trace, explain again
                     ├─ "as json" and nothing else           -> re-render the stored envelope (no call)
                     └─ anything else                        -> follow-up question (one call)
    error text or a trace                                    -> explain
    no error text                                            -> scope check (decline, or pause: "Paste the error")
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import paste as paste_mod
import smalltalk
import trace_parse
from inputs import Incoming, from_envelope, strip_markers

JSON_ONLY_MAX_WORDS = 6
_TRUST_LIBRARY = re.compile(r"\bmy (?:own )?(?:library|package|lib|module)\b|\bbug is in (?:the|my) library\b",
                            re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | explain | attach | json | followup | scope
    reply: str = ""
    paste: paste_mod.Paste | None = None
    blocks: tuple[paste_mod.CodeBlock, ...] = ()
    trust_libraries: bool = False


def plan_turn(incoming: Incoming, has_result: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    text = strip_markers(from_envelope(incoming.text) or incoming.text)
    pasted = paste_mod.split(text)
    trust = bool(_TRUST_LIBRARY.search(pasted.words or text))
    if has_result:
        return _followup_route(text, pasted, incoming.wants_json, trust)
    if has_new_trace(pasted) or trace_parse.has_error_text("\n".join((pasted.words, *pasted.traces))):
        return Route("explain", paste=pasted, trust_libraries=trust)
    return Route("scope")


def _followup_route(text: str, pasted: paste_mod.Paste, wants_json: bool, trust: bool) -> Route:
    if has_new_trace(pasted):
        return Route("explain", paste=pasted, trust_libraries=trust)
    if pasted.blocks:
        return Route("attach", blocks=pasted.blocks)
    loose = paste_mod.loose_block(text)
    if loose is not None:
        return Route("attach", blocks=(loose,))
    if wants_json and len(text.split()) <= JSON_ONLY_MAX_WORDS:
        return Route("json")
    return Route("followup")


def has_new_trace(pasted: paste_mod.Paste) -> bool:
    """A real trace (frames parsed) or a bare error line with no question mark (`ECONNREFUSED 127.0.0.1:5432`)."""
    for text in pasted.traces:
        if not trace_parse.parse(text).frameless:
            return True
    joined = "\n".join(pasted.traces)
    return "?" not in joined and trace_parse.has_typed_error(joined)


def resume_paste(request: str, answer: str) -> paste_mod.Paste:
    """The paste typed into the "Paste the error" card, taken as the trace even if it holds no error word."""
    pasted = paste_mod.split(strip_markers(answer))
    words = " ".join(w for w in (request, pasted.words) if w).strip()
    traces = pasted.traces or (answer.strip(),)
    return paste_mod.Paste(words, traces, pasted.blocks, pasted.primary if pasted.traces else 0,
                           pasted.skipped_traces, pasted.notes)


def trusts_libraries(words: str) -> bool:
    return bool(_TRUST_LIBRARY.search(words or ""))
