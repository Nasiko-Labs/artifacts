"""Pure: the in-code secret scan over a file's ADDED lines (so a secret is caught in any language), and
the two masks. A secret value is never shown whole (display keeps the first 3 characters) and never
reaches the model (it sees `[MASKED_SECRET]`)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from records import Finding

MODEL_MASK = "[MASKED_SECRET]"
_KEEP = 3
CWE_SECRET = "CWE-798"


@dataclass(frozen=True)
class SecretRule:
    rule_id: str
    pattern: re.Pattern[str]
    severity: str       # critical | high | medium
    message: str
    group: int = 0      # the regex group holding the secret value


RULES: tuple[SecretRule, ...] = (
    SecretRule("secret-private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"
                                                r"(?: BLOCK)?-----"), "critical",
               "A private key is committed in the code."),
    SecretRule("secret-cloud-access-key", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), "critical",
               "A cloud access key id is committed in the code.", 1),
    SecretRule("secret-access-token", re.compile(r"\b((?:gh[pousr]_[A-Za-z0-9]{36,})|(?:xox[abposr]-[A-Za-z0-9-]{10,})"
                                                 r"|(?:sk_live_[A-Za-z0-9]{16,})|(?:glpat-[A-Za-z0-9_-]{20,}))"),
               "critical", "An access token is committed in the code.", 1),
    SecretRule("secret-jwt", re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),
               "high", "A signed token (JWT) is committed in the code.", 1),
    SecretRule("secret-password-literal",
               re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api_?key|auth_?token|access_?token)\b[\"']?\s*[:=]\s*"
                          r"[\"']([^\"'\s]{6,})[\"']"), "medium",
               "A password or key is assigned as a string literal.", 1),
)


def scan_added(path: str, added: Iterable[tuple[int, str]]) -> tuple[Finding, ...]:
    """One finding per (rule, line) over the added lines; the snippet is the display-masked line."""
    out: list[Finding] = []
    for line_no, text in added:
        for rule in RULES:
            if rule.pattern.search(text):
                out.append(Finding(file=path, start_line=line_no, end_line=line_no, rule_id=rule.rule_id,
                                   severity=rule.severity, message=rule.message, confidence="HIGH", cwe=CWE_SECRET,
                                   snippet=mask_display(text.strip()), is_secret=True, source="secret scan"))
                break
    return tuple(out)


def _values(text: str) -> list[tuple[int, int]]:
    spans = []
    for rule in RULES:
        for m in rule.pattern.finditer(text):
            spans.append(m.span(rule.group))
    return sorted(spans)


def _apply(text: str, replace) -> str:
    spans = _values(text)
    if not spans:
        return text
    out, at = [], 0
    for start, end in spans:
        if start < at:
            continue
        out.append(text[at:start])
        out.append(replace(text[start:end]))
        at = end
    out.append(text[at:])
    return "".join(out)


def mask_display(text: str) -> str:
    """Every secret value reduced to its first 3 characters plus `****`."""
    return _apply(text or "", lambda v: v[:_KEEP] + "****")


def mask_model(text: str) -> str:
    """Every secret value replaced by `[MASKED_SECRET]`: what the model may see."""
    return _apply(text or "", lambda _v: MODEL_MASK)


def has_secret(text: str) -> bool:
    return bool(_values(text or ""))
