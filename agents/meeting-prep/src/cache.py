"""The per-conversation cache of prepared cards, for follow-ups ("more on the 2pm").

Kept in the platform's key-value state (`/api/agent-state/kv/prep:{contextId}`, the platform's default
TTL) when NASIKO_STATE_URL is injected, so a follow-up that lands on another replica or after a restart
still finds it; otherwise in process memory. A cache failure is logged and treated as a miss: it never
fails the turn."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Protocol

from nasiko_state import NasikoStateClient, NasikoStateError

log = logging.getLogger("meeting_prep.cache")

KEY_PREFIX = "prep:"
MAX_MEMORY_ENTRIES = 512


class PrepCache(Protocol):
    async def load(self, context_id: str) -> dict | None: ...

    async def save(self, context_id: str, value: dict) -> None: ...


def cache_key(context_id: str) -> str:
    return f"{KEY_PREFIX}{context_id}"


class MemoryCache:
    """Process-local fallback for local runs; bounded, oldest evicted first."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, dict] = OrderedDict()

    async def load(self, context_id: str) -> dict | None:
        return self._entries.get(cache_key(context_id))

    async def save(self, context_id: str, value: dict) -> None:
        self._entries[cache_key(context_id)] = value
        self._entries.move_to_end(cache_key(context_id))
        while len(self._entries) > MAX_MEMORY_ENTRIES:
            self._entries.popitem(last=False)


class KvCache:
    def __init__(self, client: NasikoStateClient):
        self._client = client

    async def load(self, context_id: str) -> dict | None:
        try:
            record = await self._client.aget_kv(cache_key(context_id))
        except NasikoStateError as e:
            log.warning("could not read cached cards: %s", e)
            return None
        value: Any = record.value if record is not None else None
        return value if isinstance(value, dict) else None

    async def save(self, context_id: str, value: dict) -> None:
        try:
            await self._client.aput_kv(cache_key(context_id), value)
        except NasikoStateError as e:
            log.warning("could not cache cards (follow-ups will re-read the calendar): %s", e)


def make_cache() -> PrepCache:
    try:
        client = NasikoStateClient.from_env()
    except NasikoStateError as e:
        log.warning("state API misconfigured, caching cards in memory: %s", e)
        return MemoryCache()
    return KvCache(client) if client is not None else MemoryCache()
