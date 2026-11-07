"""Pure: who "me" is and which conversations the user is in, from the identity step's JSON answer.
Every id, handle and name is kept only when it appears verbatim in this step's tool output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models import Channel, Me

MAX_CONVERSATIONS = 60


def _seen(value: Any, seen: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if text and text in seen else ""


def me_from(answer: Mapping[str, Any], seen: str, typed_handle: str = "") -> Me:
    raw = answer.get("me")
    if isinstance(raw, Mapping):
        found = Me(user_id=_seen(raw.get("user_id"), seen), handle=_seen(raw.get("handle"), seen).lstrip("@"),
                   display_name=_seen(raw.get("display_name"), seen))
        if found.known:
            return found
    return Me(handle=typed_handle.lstrip("@"), typed=True) if typed_handle else Me(typed=True)


def channels_from(answer: Mapping[str, Any], seen: str) -> tuple[Channel, ...]:
    raw = answer.get("channels")
    if not isinstance(raw, list):
        return ()
    found: dict[str, Channel] = {}
    for item in raw[:MAX_CONVERSATIONS]:
        if not isinstance(item, Mapping):
            continue
        cid = _seen(item.get("id"), seen)
        if not cid or cid in found:
            continue
        name = _seen(item.get("name"), seen).lstrip("#")
        kind = "dm" if item.get("kind") == "dm" else "channel"
        found[cid] = Channel(id=cid, name=name, kind=kind, muted=item.get("muted") is True,
                             archived=item.get("archived") is True)
    return tuple(found.values())
