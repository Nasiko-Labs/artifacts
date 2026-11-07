"""The brief's model calls: every call carries the inbound traceparent."""

import logging

from brief import validate_brief
from prompts import INPUT_LABELS, REPAIR_INSTRUCTION, SYSTEM_PROMPT
from telemetry import inbound_traceparent

logger = logging.getLogger(__name__)

TEMPERATURE = 0.3
MAX_TOKENS = 3000
UNCITED_WARNING = (
    "\n\n---\n_Check before use: {problems} These points are not cited to a pasted item._"
)


def traceparent_headers() -> dict[str, str]:
    """The headers that tie a model call to the platform flow (else the router answers 403).

    Prefers the traceparent the platform sent us; falls back to the current OTel context.
    """
    inbound = inbound_traceparent.get()
    if inbound:
        return {"traceparent": inbound}
    carrier: dict[str, str] = {}
    try:
        from opentelemetry.propagate import inject
        inject(carrier)
    except ImportError:
        logger.warning("opentelemetry not installed; model call has no traceparent")
    return {"traceparent": carrier["traceparent"]} if "traceparent" in carrier else {}


async def _complete(llm, model: str, messages: list[dict], headers: dict[str, str]) -> str:
    resp = await llm.chat.completions.create(
        model=model,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        extra_headers=headers,
    )
    return resp.choices[0].message.content or ""


async def generate_brief(llm, model: str, query: str, headers: dict[str, str]) -> str:
    """Write the brief; if a talking point is uncited, ask once for a rewrite.

    A brief that still breaks the rules is returned with a visible warning, never silently.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    brief = await _complete(llm, model, messages, headers)
    problems = validate_brief(brief)
    if not problems:
        return brief

    logger.info("brief failed validation, retrying once: %s", problems)
    repair = REPAIR_INSTRUCTION.format(
        problems="\n".join(f"- {p}" for p in problems),
        labels=", ".join(f"[{label} #N]" for label in INPUT_LABELS),
    )
    retry_messages = [
        *messages,
        {"role": "assistant", "content": brief},
        {"role": "user", "content": repair},
    ]
    retried = await _complete(llm, model, retry_messages, headers)
    remaining = validate_brief(retried)
    if not remaining:
        return retried

    logger.warning("brief still fails validation after retry: %s", remaining)
    return retried + UNCITED_WARNING.format(problems=" ".join(remaining))
