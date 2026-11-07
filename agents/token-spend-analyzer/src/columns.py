"""Pure: which header plays which role. Synonyms cover the OpenAI usage/cost export, the Anthropic usage export,
LiteLLM-style gateway spend logs and generic tables. The first synonym present wins (list order is priority).
A user's answer to the column question is an override that always wins."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

ROLES = ("model", "team", "task", "user", "ts", "input", "output", "cached_in", "cache_write", "requests", "cost",
         "status", "attempt")
NUMERIC_ROLES = ("input", "output", "cached_in", "cache_write", "requests", "cost", "attempt")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "model": ("model", "model_name", "snapshot_id", "snapshot", "model_id", "model_group", "llm_model"),
    "team": ("team", "team_name", "team_alias", "team_id", "project", "project_name", "project_id", "workspace",
             "workspace_name", "workspace_id", "org", "organization", "department", "key_alias", "api_key_name",
             "api_key_id", "api_key", "agent", "agent_name"),
    "task": ("task", "feature", "use_case", "usecase", "tag", "tags", "request_tags", "operation", "endpoint", "app",
             "application", "call_type", "route"),
    "user": ("user", "user_id", "end_user", "user_email"),
    "ts": ("date", "timestamp", "day", "start_time", "starttime", "start_date", "created_at", "time", "bucket_start",
           "usage_date"),
    "input": ("input_tokens", "prompt_tokens", "n_context_tokens_total", "context_tokens", "tokens_in",
              "uncached_input_tokens"),
    "output": ("output_tokens", "completion_tokens", "n_generated_tokens_total", "generated_tokens", "tokens_out"),
    "cached_in": ("input_cached_tokens", "cached_tokens", "cache_read_input_tokens", "cache_read_tokens",
                  "cached_input_tokens", "n_cached_tokens_total"),
    "cache_write": ("cache_creation_input_tokens", "cache_creation_tokens", "cache_write_tokens"),
    "requests": ("requests", "n_requests", "num_model_requests", "num_requests", "calls", "request_count",
                 "operations", "count"),
    "cost": ("cost", "spend", "amount", "amount_value", "usd", "cost_usd", "total_cost", "cost_in_usd", "price"),
    "status": ("status", "outcome", "success", "error", "error_type"),
    "attempt": ("attempt", "attempt_number", "retry", "retries", "retry_count"),
}
# Anthropic's export counts cache reads/writes outside input_tokens; every other shape counts them inside.
INPUT_EXCLUDES_CACHE = frozenset({"cache_read_input_tokens", "cache_creation_input_tokens"})
_NON_WORD = re.compile(r"[^a-z0-9]+")


def norm(header: str) -> str:
    return _NON_WORD.sub("_", str(header or "").casefold()).strip("_")


@dataclass(frozen=True)
class ColumnMap:
    roles: dict[str, str] = field(default_factory=dict)     # role → header
    input_excludes_cache: bool = False

    def has(self, role: str) -> bool:
        return role in self.roles


def resolve(headers: Iterable[str], overrides: Mapping[str, str] | None = None) -> ColumnMap:
    headers = [str(h) for h in headers]
    by_norm: dict[str, str] = {}
    for h in headers:
        by_norm.setdefault(norm(h), h)
    roles: dict[str, str] = {}
    used: set[str] = set()
    for role, header in (overrides or {}).items():
        if header in headers:
            roles[role], used = header, used | {header}
    for role in ROLES:
        if role in roles or role in (overrides or {}):
            continue
        hit = next((by_norm[s] for s in SYNONYMS[role] if s in by_norm and by_norm[s] not in used), None)
        if hit is not None:
            roles[role] = hit
            used.add(hit)
    excludes = any(norm(roles.get(r, "")) in INPUT_EXCLUDES_CACHE for r in ("cached_in", "cache_write"))
    return ColumnMap(roles, excludes)


def match_header(answer: str, headers: Iterable[str]) -> str | None:
    """The header the user named (exact, then normalised)."""
    text = (answer or "").strip().strip("`'\"")
    headers = list(headers)
    if text in headers:
        return text
    wanted = norm(text)
    return next((h for h in headers if norm(h) == wanted), None) if wanted else None
