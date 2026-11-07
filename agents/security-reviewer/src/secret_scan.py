"""Pure: find secrets in pasted text and replace each with a placeholder before anything reaches the model.

Named `secret_scan` (not `secrets`) so it never shadows the standard library module. Each distinct value
gets one placeholder `<<SECRET:label:n>>`; people see only its first 4 characters (`AKIA…`). The originals
live only in this turn's memory and are never checkpointed, logged, sent or rendered."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

MIN_GENERIC_ENTROPY = 3.0
DISPLAY_CHARS = 4
CRITICAL_LABELS = frozenset({"private-key", "stripe-live-key"})

# (label, pattern, value group): the group is what gets masked.
_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"), 0),
    ("aws-key", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), 1),
    ("aws-secret", re.compile(r"(?i)aws_?secret\w*[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"), 1),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"), 1),
    ("slack-token", re.compile(r"\b(xox[abprs]-[A-Za-z0-9-]{10,})\b"), 1),
    ("stripe-live-key", re.compile(r"\b((?:sk|rk)_live_[0-9A-Za-z]{16,})\b"), 1),
    ("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})(?![0-9A-Za-z_-])"), 1),
    ("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), 1),
    ("connection-string", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@'\"<>]+:([^\s@/'\"<>]{3,})@"), 1),
    ("password", re.compile(r"(?i)\b[\w-]*(?:password|passwd|pwd|secret|api[_-]?key|apikey|token|auth[_-]?key)[\w-]*"
                            r"[\"']?\s*[:=]\s*([\"'])([^\"'\n]{8,})\1"), 2),
)
_PLACEHOLDER_VALUE = re.compile(r"(?i)^(?:changeme|change_me|xxx+|\*+|\.\.\.|example|dummy|redacted|your[_-].*|"
                                r"<[^>]*>|\$\{[^}]*\}|\{\{.*\}\}|%\(.*\)s|os\.environ.*|process\.env.*|none|null|"
                                r"password\d*|secret|test|placeholder|todo|fixme)$")
_KEY_BODY = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
PLACEHOLDER = re.compile(r"<<SECRET:([a-z-]+):(\d+)>>")


def entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum(c / total * math.log2(c / total) for c in counts.values()) if total else 0.0


@dataclass
class Masker:
    """Stateful for one turn: the same value always maps to the same placeholder."""

    placeholders: dict[str, str] = field(default_factory=dict)   # original -> placeholder
    displays: dict[str, str] = field(default_factory=dict)       # placeholder -> "AKIA…"
    labels: dict[str, str] = field(default_factory=dict)         # placeholder -> label
    in_key: bool = False

    def mask_line(self, text: str) -> tuple[str, list[str]]:
        """(masked line, placeholders first seen or used on it)."""
        if self.in_key:
            if _KEY_END.search(text):
                self.in_key = False
                return text, []
            if _KEY_BODY.match(text):
                return "", []
        found: list[str] = []
        for label, pattern, group in _PATTERNS:
            text = pattern.sub(lambda m, lb=label, g=group: self._replace(m, lb, g, found), text)
        return text, found

    def mask_text(self, text: str) -> tuple[str, list[str]]:
        masked, found = [], []
        for line in (text or "").split("\n"):
            out, hits = self.mask_line(line)
            masked.append(out)
            found += hits
        return "\n".join(masked), found

    def _replace(self, match: re.Match, label: str, group: int, found: list[str]) -> str:
        value = match.group(group)
        if "<<SECRET:" in match.group(0):
            return match.group(0)
        if label == "private-key":
            self.in_key = True
        elif label in ("password", "connection-string") and not _is_secret_value(value):
            return match.group(0)
        placeholder = self._placeholder(value, label)
        found.append(placeholder)
        start, end = match.start(group) - match.start(0), match.end(group) - match.start(0)
        whole = match.group(0)
        return whole[:start] + placeholder + whole[end:]

    def _placeholder(self, value: str, label: str) -> str:
        if value not in self.placeholders:
            placeholder = f"<<SECRET:{label}:{len(self.placeholders) + 1}>>"
            self.placeholders[value] = placeholder
            self.displays[placeholder] = value[:DISPLAY_CHARS] + "…"
            self.labels[placeholder] = label
        return self.placeholders[value]


def _is_secret_value(value: str) -> bool:
    stripped = value.strip()
    if _PLACEHOLDER_VALUE.match(stripped) or "${" in stripped or stripped.startswith(("<", "{{", "%(")):
        return False
    return entropy(stripped) >= MIN_GENERIC_ENTROPY


def unmask_display(text: str, displays: dict[str, str]) -> str:
    """Placeholders -> their masked display form (`AKIA…`), for anything shown to people."""
    return PLACEHOLDER.sub(lambda m: displays.get(m.group(0), "[secret]"), text or "")
