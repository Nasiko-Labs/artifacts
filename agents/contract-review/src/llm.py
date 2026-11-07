"""OpenAI-compatible LLM access for the contract-review agent.

The model comes from the platform's LLM router (a pinned config), reached through
OPENAI_BASE_URL / OPENAI_API_KEY. Every call carries the inbound `traceparent` (header and bound
OTel context) so the router attributes it to the live flow. Nothing calls a model at import or
at startup.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from openai import AsyncOpenAI

from nasiko_trace import bound_traceparent

DEFAULT_MODEL = "gpt-4o"
MODEL_ENV = "OPENAI_MODEL"
# Placeholder so the agent starts (card, health) without a key; the first call then fails loudly.
_UNSET_KEY = "unset"


class LlmOutputError(ValueError):
    """The model's reply was not the JSON object the schema asked for."""


def model_from_env(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return (source.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL


def client_from_env(http_client: Any = None) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ.get("OPENAI_API_KEY") or _UNSET_KEY,
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        http_client=http_client,
    )


async def complete_json(
    client: AsyncOpenAI,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    schema_name: str,
    schema: Mapping[str, Any],
    traceparent: str,
) -> dict:
    """One structured-output call (strict JSON schema, temperature 0) → the parsed object."""
    with bound_traceparent(traceparent):
        response = await client.chat.completions.create(
            model=model,
            messages=[dict(m) for m in messages],
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": dict(schema)},
            },
            extra_headers={"traceparent": traceparent},
        )
    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
    except ValueError as e:
        raise LlmOutputError(f"model reply is not JSON: {content[:200]!r}") from e
    if not isinstance(parsed, dict):
        raise LlmOutputError("model reply is not a JSON object")
    return parsed
