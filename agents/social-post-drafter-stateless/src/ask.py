"""Pure: the one question this agent asks (material missing), as a plain reply, and reading the next message
as its answer. Nothing pauses and nothing is stored: the answer is recognised because the most recent reply in
the platform's chat history is this question, and the request it was about is the user turn before it."""

from __future__ import annotations

import history
from inputs import Request

CANCEL = "cancel"
NO_DRAFT = "No posts drafted."
LEAD = ("What should the post be about? Paste the announcement, notes or idea in your next message (best), or "
        "say which kind of post: a launch or announcement, a lesson or tip, an opinion, or a hiring or event post.")
OPTIONS = (("a launch or announcement", "announce"), ("a lesson or tip", "educate"), ("an opinion", "opinion"),
           ("a hiring or event post", "hire"))


def answering(turns: tuple[history.Turn, ...]) -> bool:
    """True when the user's message answers this agent's question."""
    return history.last_assistant(turns).startswith(LEAD[:40])


def answer(request: Request) -> Request | str:
    """The request to draft now (the earlier ask plus this answer), or the fixed reply on cancel."""
    text = request.text.strip()
    if not text or text.casefold().rstrip(".!") == CANCEL:
        return NO_DRAFT
    earlier = history.user_before_last_assistant(request.history)
    for label, goal in OPTIONS:
        if text.casefold().rstrip(".!") == label:
            return Request(text=f"{earlier}\n\nKind of post: {label}".strip(), org=request.org, goal=goal,
                           asked=True, history=request.history)
    return Request(text=f"{earlier}\n\n{text}".strip(), org=request.org, asked=True, history=request.history)
