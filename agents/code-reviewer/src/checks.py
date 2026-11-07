"""Pure: every comment the model returns is checked against the parsed diff before anyone sees it.

(1) `file` is a path in the diff; (2) `line` is an ADDED line of that file; (3) `quote` matches that line
(normalised); otherwise a unique matching added line within +/-5 lines is used (snapped), else the comment is
dropped; (4) severity caps (severity.py); (5) formatting-only comments dropped unless "nits too"; (8) any path
or identifier the comment names must appear in the diff or the standards, else that sentence is removed.
Suggestions are checked in suggestion.py."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import severity as sev
import suggestion as sug
from diff_parse import FileDiff

SNAP_WINDOW = 5
MIN_PARTIAL_QUOTE = 12
MAX_COMMENT_CHARS = 1_200
AXES = ("correctness", "readability", "architecture", "security", "performance", "standards")
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_FORMATTING = re.compile(r"\bwhitespace\b|\bindent(?:ation|ed)?\b|\btrailing comma|\bsemicolons?\b|\bquotes?\b|"
                         r"\bquote style\b|\bline length\b|\bimport order\b|\blong line\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_PATHLIKE = re.compile(r"(?<![\w`/])((?:[\w.-]+/)*[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|cs|kt|swift|"
                       r"cpp|sql|ya?ml|json|toml))\b")
_TICKED = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`")
_KNOWN_WORDS = frozenset({"None", "True", "False", "null", "undefined", "nil", "self", "this", "true", "false",
                          "NaN", "Optional", "async", "await", "try", "except", "finally", "return", "raise", "if",
                          "else", "for", "while", "with", "const", "let", "var"})


def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").translate(_SMART).split())


def quote_matches(quote: str, line: str) -> bool:
    q, t = norm(quote), norm(line)
    if not q:
        return False
    return q == t or (len(q) >= MIN_PARTIAL_QUOTE and q in t)


@dataclass(frozen=True)
class Comment:
    file: str
    line: int
    end_line: int | None
    quote: str
    severity: str
    category: str
    axis: str
    comment: str
    suggestion: str | None
    pre_existing: bool = False
    rule: str | None = None
    snapped: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> Comment:
        fields = {k: data.get(k) for k in Comment.__dataclass_fields__}
        return Comment(**{**fields, "line": int(fields["line"]), "pre_existing": bool(fields["pre_existing"]),
                          "snapped": bool(fields["snapped"]),
                          "end_line": int(fields["end_line"]) if fields["end_line"] is not None else None})


@dataclass(frozen=True)
class Context:
    files: Mapping[str, FileDiff]
    vocabulary: str          # normalised diff text + standards: what a comment may name
    standards: str = ""
    nits: bool = False


def check(raw: object, ctx: Context) -> tuple[Comment | None, str]:
    """(comment, "") when it passes; (None, reason) when dropped. Reason "formatting" is a skip, not an error."""
    if not isinstance(raw, Mapping):
        return None, "not an object"
    path = _path(raw.get("file"), ctx.files)
    if path is None:
        return None, f"file {raw.get('file')!r} is not in the diff"
    added = ctx.files[path].added
    line, quote = _int(raw.get("line")), str(raw.get("quote") or "")
    target = _locate(line, quote, added)
    if target is None:
        return None, f"line {line} of {path} is not an added line whose text matches the quote {quote[:80]!r}"
    text = clean_comment(str(raw.get("comment") or ""), ctx, raw.get("suggestion"))
    if not text:
        return None, "the comment names nothing that is in the diff"
    if not ctx.nits and _FORMATTING.search(text):
        return None, "formatting"
    category = sev.normal_category(raw.get("category"))
    axis = str(raw.get("axis") or "").casefold()
    axis = axis if axis in AXES else "correctness"
    rule = _rule(raw.get("rule"), ctx.standards)
    pre = bool(raw.get("pre_existing"))
    level = sev.capped(sev.normal_severity(raw.get("severity")), category, axis, text, pre_existing=pre,
                       rule_verified=rule is not None)
    end = _end_line(_int(raw.get("end_line")), line, target, added)
    original = "\n".join(added[n] for n in range(target, (end or target) + 1))
    fix = sug.checked(raw.get("suggestion"), original, path)
    return Comment(path, target, end, added[target], level, category, axis, text, fix, pre, rule,
                   snapped=target != line), ""


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _path(value: object, files: Mapping[str, FileDiff]) -> str | None:
    text = str(value or "").strip().strip("`")
    for candidate in (text, text.removeprefix("a/"), text.removeprefix("b/"), text.removeprefix("./")):
        if candidate in files:
            return candidate
    return None


def _locate(line: int | None, quote: str, added: Mapping[int, str]) -> int | None:
    if line is not None and line in added and quote_matches(quote, added[line]):
        return line
    if line is None:
        return None
    near = [n for n in range(line - SNAP_WINDOW, line + SNAP_WINDOW + 1)
            if n in added and quote_matches(quote, added[n])]
    return near[0] if len(near) == 1 else None


def _end_line(end: int | None, line: int | None, target: int, added: Mapping[int, str]) -> int | None:
    if end is None or line is None or end <= line:
        return None
    shifted = end + (target - line)
    if shifted - target >= sug.MAX_SUGGESTION_LINES or any(n not in added for n in range(target, shifted + 1)):
        return None
    return shifted


def _rule(value: object, standards: str) -> str | None:
    rule = str(value or "").strip().strip('"')
    return rule if rule and standards and norm(rule).casefold() in norm(standards).casefold() else None


def clean_comment(text: str, ctx: Context, fix: object) -> str:
    """Removes each sentence that names a path or identifier found neither in the diff nor in the standards."""
    allowed = ctx.vocabulary + "\n" + norm(str(fix or ""))
    kept = [s for s in _SENTENCE.split(text.strip()) if _grounded(s, allowed)]
    return " ".join(kept).strip()[:MAX_COMMENT_CHARS]


def _grounded(sentence: str, allowed: str) -> bool:
    for path in _PATHLIKE.findall(sentence):
        if path not in allowed:
            return False
    for name in _TICKED.findall(sentence):
        base = name.split(".")[-1]
        if name not in _KNOWN_WORDS and base not in _KNOWN_WORDS and base not in allowed:
            return False
    return True
