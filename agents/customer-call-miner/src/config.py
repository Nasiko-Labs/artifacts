"""Settings, every one with a built-in default: nothing is required. The environment only overrides.

    OWN_DOMAINS          comma list of the company's own email domains (added to the domains the
                         call-recording tool marks as internal)
    FREE_MAIL_DOMAINS    comma list replacing the built-in free-mail list (never matched to an account)
    COMPETITOR_ALIASES   "alias=Canonical; other alias=Canonical"
    CALL_MINER_MAX_CALLS calls analysed per turn (default 12)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

DEFAULT_FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com",
    "gmx.de", "mail.com", "zoho.com", "yandex.com", "fastmail.com", "hey.com",
})
MAX_CALLS = 12
CHUNK_CHARS = 8000
MAX_CHUNKS_PER_CALL = 3
EXTRACT_CONCURRENCY = 4
MAX_ITEMS_PER_CHUNK = 8
MAX_LABELS = 150
MAX_QUOTE_CHARS = 300
DIGEST_MAX_CHARS = 3500
EXTRACT_STOP_SECS = 330.0  # of the 420 s mining-turn budget (the flow limit is 600 s)
MAX_DOMAINS = 40


@dataclass(frozen=True)
class Config:
    own_domains: frozenset[str] = frozenset()
    free_mail: frozenset[str] = DEFAULT_FREE_MAIL
    competitor_aliases: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    max_calls: int = MAX_CALLS
    chunk_chars: int = CHUNK_CHARS
    max_chunks_per_call: int = MAX_CHUNKS_PER_CALL
    extract_concurrency: int = EXTRACT_CONCURRENCY
    max_items_per_chunk: int = MAX_ITEMS_PER_CHUNK
    max_labels: int = MAX_LABELS
    max_quote_chars: int = MAX_QUOTE_CHARS
    digest_max_chars: int = DIGEST_MAX_CHARS
    extract_stop_secs: float = EXTRACT_STOP_SECS
    max_domains: int = MAX_DOMAINS

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        free = _domains(source.get("FREE_MAIL_DOMAINS"))
        return Config(own_domains=_domains(source.get("OWN_DOMAINS")), free_mail=free or DEFAULT_FREE_MAIL,
                      competitor_aliases=MappingProxyType(_aliases(source.get("COMPETITOR_ALIASES"))),
                      max_calls=_int(source.get("CALL_MINER_MAX_CALLS"), MAX_CALLS, 1, 30))


def _domains(raw: str | None) -> frozenset[str]:
    return frozenset(d.strip().casefold().removeprefix("www.") for d in (raw or "").split(",") if d.strip())


def _aliases(raw: str | None) -> dict[str, str]:
    pairs = (part.split("=", 1) for part in (raw or "").split(";") if "=" in part)
    return {alias.strip().casefold(): name.strip() for alias, name in pairs if alias.strip() and name.strip()}


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(raw))) if raw else default
    except ValueError:
        return default
