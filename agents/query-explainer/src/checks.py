"""Pure: the code checks on model output.

(1) every backticked name in model text exists in the SQL or DDL; (2) every evidence string is a substring of
the SQL (normalised: NFKC, straight quotes, collapsed whitespace, case-folded); (3) the model cannot change a
rule finding's id, severity or evidence: only `so_what` and `hint` are taken; (4) extra assumptions with
unverified evidence are dropped; (5) the narrative must mention every CTE and every table read; (6) hints are
labelled untested by code."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import replace

from sqlglot import exp
from sqlglot.tokens import Tokenizer

from findings import Finding

MAX_EXTRA_ASSUMPTIONS = 3
MAX_SENTENCE_CHARS = 400
UNTESTED = " (untested)"
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})
_BACKTICKED = re.compile(r"`([^`\n]{1,200})`")
_WORD = re.compile(r"[A-Za-z_][\w$]*")
_EMPTY_STEP = re.compile(r"^\s*\d{1,2}\.\s*$")
SQL_WORDS = frozenset(w.casefold() for key in Tokenizer.KEYWORDS for w in key.split()) | frozenset(
    ["null", "true", "false", "asc", "desc", "nulls", "first", "last", "interval", "date", "timestamp"])


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").translate(_QUOTES).split()).casefold()


def known_names(statements: Iterable[exp.Expression]) -> frozenset[str]:
    names: set[str] = set()
    for statement in statements:
        for node in statement.walk():
            if isinstance(node, exp.Identifier):
                names.add(node.name.casefold())
            elif isinstance(node, exp.Anonymous):
                names.add(str(node.this).casefold())
            elif isinstance(node, exp.Func):
                names.add(node.sql_name().casefold())
            elif isinstance(node, exp.Literal) and node.is_string:
                names.add(node.this.casefold())
    return frozenset(names)


def unknown_names(text: str, names: frozenset[str], corpus: str) -> list[str]:
    """Backticked tokens in `text` that are neither a fragment of the SQL nor made only of known names."""
    flat = normalize(corpus)
    bad = []
    for token in _BACKTICKED.findall(text or ""):
        if normalize(token) in flat:
            continue
        words = [w.casefold() for w in _WORD.findall(token)]
        if any(w not in names and w not in SQL_WORDS for w in words):
            bad.append(token)
    return bad


def missing_mentions(text: str, must: Iterable[str]) -> list[str]:
    flat = (text or "").casefold()
    return [n for n in must if not re.search(rf"(?<![\w]){re.escape(n.casefold())}(?![\w])", flat)]


def evidence_ok(evidence: str, corpus: str) -> bool:
    needle = normalize(evidence)
    return len(needle) >= 3 and needle in normalize(corpus)


def steps(plain: str) -> list[str]:
    """The narrative as lines: one numbered step per line, then the 'In short' line."""
    text = re.sub(r"\s+(?=(?:\d{1,2}\. |In short:))", "\n", (plain or "").strip())
    return [line.strip() for line in text.splitlines() if line.strip() and not _EMPTY_STEP.match(line)]


def drop_unknown(lines: list[str], names: frozenset[str], corpus: str) -> tuple[list[str], int]:
    kept = [ln for ln in lines if not unknown_names(ln, names, corpus)]
    return kept, len(lines) - len(kept)


def sentence(text, names: frozenset[str], corpus: str) -> str:
    """One checked model sentence, or "" when it names something not in the SQL."""
    value = " ".join(str(text or "").split())[:MAX_SENTENCE_CHARS]
    return "" if not value or unknown_names(value, names, corpus) else value


def merge_notes(findings: list[Finding], notes, names: frozenset[str], corpus: str) -> tuple[list[Finding], int]:
    """Rule findings with the model's checked `so_what`/`hint`; id, severity and evidence stay the code's."""
    notes = notes if isinstance(notes, dict) else {}
    out, dropped = [], 0
    for f in findings:
        note = notes.get(f.cid) if isinstance(notes.get(f.cid), dict) else {}
        so_what = sentence(note.get("so_what"), names, corpus)
        hint = sentence(note.get("hint"), names, corpus)
        dropped += int(bool(note.get("so_what")) and not so_what) + int(bool(note.get("hint")) and not hint)
        out.append(replace(f, so_what=so_what, hint=(hint.rstrip(".") + UNTESTED) if hint else ""))
    return out, dropped


def extra_assumptions(raw, names: frozenset[str], corpus: str) -> tuple[list[Finding], int]:
    items = raw if isinstance(raw, list) else []
    kept: list[Finding] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = sentence(item.get("text"), names, corpus)
        evidence = " ".join(str(item.get("evidence") or "").split())
        if len(kept) >= MAX_EXTRA_ASSUMPTIONS or not text or not evidence_ok(evidence, corpus):
            dropped += 1
            continue
        kept.append(Finding("M", "assumption", text, evidence, source="model", cid=f"M-{len(kept) + 1}"))
    return kept, dropped
