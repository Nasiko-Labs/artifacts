"""A2A pause metadata for Nasiko human-in-the-loop (copied in from the MCP kit; trimmed and owned by
this agent: only `question_metadata` is kept: the one question asks for the repository).

`question_metadata` builds the structured-question block for an agent's own `input-required`
(oss/types/src/a2a.rs::hoist_structured_options). The platform silently drops a malformed block
and falls back to a plain question, so this validates against the same limits and raises.
One question per pause.
"""

from __future__ import annotations

from collections.abc import Sequence

MAX_OPTIONS = 20
MAX_OPTION_LABEL_LEN = 200
MAX_OPTION_DESCRIPTION_LEN = 2000
MAX_QUESTION_HEADER_LEN = 200


def question_metadata(
    header: str,
    options: Sequence[tuple[str, str]],
    multi_select: bool = False,
    allow_custom_input: bool = True,
) -> dict:
    """Structured choices for one `input-required` question. Raises ValueError if the platform
    would drop the block (empty/long header, 0 or >20 options, empty/long/duplicate labels). An
    over-long description is also refused, although the platform would drop only that
    description. Lengths are UTF-8 bytes, as the platform (Rust `str::len`) measures them."""
    clean_header = header.strip()
    if not clean_header or _bytes(clean_header) > MAX_QUESTION_HEADER_LEN:
        raise ValueError(f"header must be 1-{MAX_QUESTION_HEADER_LEN} UTF-8 bytes")
    if not options or len(options) > MAX_OPTIONS:
        raise ValueError(f"options must have 1-{MAX_OPTIONS} entries, got {len(options)}")
    return {
        "header": clean_header,
        "options": _validated_options(options),
        "multi_select": bool(multi_select),
        "allow_custom_input": bool(allow_custom_input),
    }


def _validated_options(options: Sequence[tuple[str, str]]) -> list[dict]:
    seen: set[str] = set()
    validated = []
    for label, description in options:
        clean_label = label.strip()
        if not clean_label or _bytes(clean_label) > MAX_OPTION_LABEL_LEN:
            raise ValueError(f"option label must be 1-{MAX_OPTION_LABEL_LEN} UTF-8 bytes: {label!r}")
        if clean_label in seen:
            raise ValueError(f"duplicate option label: {clean_label!r}")
        if _bytes(description) > MAX_OPTION_DESCRIPTION_LEN:
            raise ValueError(f"description of {clean_label!r} exceeds {MAX_OPTION_DESCRIPTION_LEN} UTF-8 bytes")
        seen.add(clean_label)
        option = {"label": clean_label}
        if description:
            option["description"] = description
        validated.append(option)
    return validated


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))
