"""Pure: added lines that look like a secret (key formats, private-key headers, a literal password or token
assignment with enough entropy). Any hit refuses the whole pull request; hits are shown masked."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

MIN_ENTROPY = 3.0
MIN_SECRET_CHARS = 12

_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
)
_ASSIGNED = re.compile(r"(?i)\b(?:password|passwd|secret|api_?key|token|private_?key)\b\s*[:=]\s*[\"']([^\"']+)[\"']")


def entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def mask(text: str) -> str:
    return text[:4] + "…" if len(text) > 4 else "…"


def hits(added_lines: Iterable[str]) -> list[str]:
    """The masked secrets found in the added lines."""
    found = []
    for line in added_lines:
        for pattern in _PATTERNS:
            found += [mask(m.group(0)) for m in pattern.finditer(line)]
        for match in _ASSIGNED.finditer(line):
            value = match.group(1)
            if len(value) >= MIN_SECRET_CHARS and entropy(value) >= MIN_ENTROPY:
                found.append(mask(value))
    return found
