"""Pure: find and mask key-shaped secrets in pasted text (API keys, GitHub/Slack tokens, AWS key ids,
JWTs, bearer tokens), so they never reach the model or the reply."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS = (
    ("an OpenAI-style API key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("a JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("a bearer token", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{20,}")),
)
VISIBLE_PREFIX = 4
MASK = "…[masked]"


@dataclass(frozen=True)
class Masked:
    text: str
    kinds: tuple[str, ...]  # what was found, e.g. ("an OpenAI-style API key",)

    @property
    def found(self) -> bool:
        return bool(self.kinds)


def mask(text: str) -> Masked:
    out, kinds = text or "", []
    for kind, pattern in _PATTERNS:
        if pattern.search(out):
            kinds.append(kind)
            out = pattern.sub(lambda m: m.group(0)[:VISIBLE_PREFIX] + MASK, out)
    return Masked(out, tuple(kinds))


def warning(kinds: tuple[str, ...]) -> str:
    what = ", ".join(dict.fromkeys(kinds))
    return (f"Heads up: you pasted what looks like {what}. I've masked it here, but treat it as exposed: "
            "rotate it now and store the new one as a secret instead of pasting it.")
