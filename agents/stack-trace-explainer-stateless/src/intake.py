"""Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and no turn pauses:
the earlier trace, code and answer come from the platform's chat history.

    greeting / small talk                                    -> canned intro (no call)
    answering our "paste the error" reply                    -> the message is the trace (or "cancel")
    a trace in the history ─┬─ a new trace pasted            -> explain it (turn 1 again)
                            ├─ code pasted, no trace         -> attach to that trace, explain again (one call)
                            ├─ "as json" and nothing else    -> explain that trace again, as JSON
                            └─ anything else                 -> follow-up (relatedness check unless it names the error)
    error text or a trace                                    -> explain
    an earlier explanation, but its trace was trimmed        -> say so, ask for the trace again (no call)
    no error text                                            -> scope check (decline, or ask "Paste the error")
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import history
import paste as paste_mod
import prompts
import smalltalk
import trace_parse
from inputs import Incoming, from_envelope, strip_markers

JSON_ONLY_MAX_WORDS = 6
REQUEST_MAX_CHARS = 500
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\W*$")
_TRUST_LIBRARY = re.compile(r"\bmy (?:own )?(?:library|package|lib|module)\b|\bbug is in (?:the|my) library\b",
                            re.IGNORECASE)


@dataclass(frozen=True)
class Earlier:
    """The newest trace the user pasted that is still in the history, the code sent with or after it, and the
    newest reply after it (the earlier answer a follow-up builds on; "" when trimmed)."""
    paste: paste_mod.Paste
    blocks: tuple[paste_mod.CodeBlock, ...]
    trust_libraries: bool
    last_reply: str


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | explain | attach | json | followup | scope
    reply: str = ""
    paste: paste_mod.Paste | None = None
    blocks: tuple[paste_mod.CodeBlock, ...] = ()
    trust_libraries: bool = False
    earlier: Earlier | None = None


def plan_turn(incoming: Incoming) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    turns = incoming.history
    if history.last_assistant(turns).startswith(prompts.ASK_TRACE_TEXT[:40]):
        return _answer_to_ask(incoming.text, turns)
    text = strip_markers(from_envelope(incoming.text) or incoming.text)
    pasted = paste_mod.split(text)
    trust = bool(_TRUST_LIBRARY.search(pasted.words or text))
    earlier = earlier_trace(turns)
    if earlier is not None:
        return _followup_route(text, pasted, incoming.wants_json, trust, earlier)
    if _has_error(pasted):
        return Route("explain", paste=pasted, trust_libraries=trust)
    if any(prompts.READ_ONLY_NOTE[:40] in t.text for t in turns if t.role == history.ASSISTANT):
        return Route("reply", prompts.TRACE_TRIMMED)
    return Route("scope")


def _answer_to_ask(answer: str, turns: tuple[history.Turn, ...]) -> Route:
    """The message after our "paste the error" reply: any text is taken as the trace; "cancel" stops."""
    text = (answer or "").strip()
    if not text or _CANCEL.match(" ".join(text.casefold().split())):
        return Route("reply", prompts.CANCELLED_REPLY)
    request = history.user_before_last_assistant(turns)[:REQUEST_MAX_CHARS]
    pasted = resume_paste(request, text)
    return Route("explain", paste=pasted, trust_libraries=trusts_libraries(pasted.words))


def _followup_route(text: str, pasted: paste_mod.Paste, wants_json: bool, trust: bool, earlier: Earlier) -> Route:
    if has_new_trace(pasted):
        return Route("explain", paste=pasted, trust_libraries=trust)
    if pasted.blocks:
        return Route("attach", blocks=pasted.blocks, earlier=earlier)
    loose = paste_mod.loose_block(text)
    if loose is not None:
        return Route("attach", blocks=(loose,), earlier=earlier)
    if wants_json and len(text.split()) <= JSON_ONLY_MAX_WORDS:
        return Route("json", paste=earlier.paste, trust_libraries=earlier.trust_libraries, earlier=earlier)
    return Route("followup", earlier=earlier)


def _has_error(pasted: paste_mod.Paste) -> bool:
    return has_new_trace(pasted) or trace_parse.has_error_text("\n".join((pasted.words, *pasted.traces)))


def earlier_trace(turns: tuple[history.Turn, ...]) -> Earlier | None:
    """Newest first: the user turn with a trace, and the code pasted with or after it."""
    later_blocks: list[paste_mod.CodeBlock] = []
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.role != history.USER:
            continue
        text = strip_markers(from_envelope(turn.text) or turn.text)
        pasted = paste_mod.split(text)
        if _has_error(pasted):
            replies = [t.text for t in turns[index + 1:] if t.role == history.ASSISTANT]
            blocks = (*pasted.blocks, *reversed(later_blocks))
            return Earlier(pasted, blocks, bool(_TRUST_LIBRARY.search(pasted.words or text)),
                           replies[-1] if replies else "")
        loose = paste_mod.loose_block(text)
        later_blocks += reversed(pasted.blocks or ((loose,) if loose is not None else ()))
    return None


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
