"""Pure: telling a model that could not be reached from a model that answered badly.

A crew failure whose cause chain holds a network, HTTP-status or timeout error is a transport failure
(`llm.LlmError`: "couldn't reach the model"); anything else (an empty answer, say) means the model answered but
the answer could not be used (`llm.UnusableAnswer`)."""

from __future__ import annotations

import httpx
import openai

TRANSPORT_ERRORS = (openai.APIError, httpx.HTTPError, TimeoutError, ConnectionError)


def is_transport(error: BaseException) -> bool:
    """True when the error (or anything it was raised from) is a network, HTTP-status or timeout failure."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, TRANSPORT_ERRORS):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
