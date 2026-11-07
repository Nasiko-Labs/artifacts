"""Pure: the code checks on the model's draft. Every path, symbol, number and ticket reference must appear in
the diff, the facts or the user's context (normalised: NFKC, straight quotes, collapsed whitespace, casefold);
result claims ("all tests pass") survive only when the user said them. A failing sentence or item is dropped
and counted, never silently."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from facts import Facts
from kinds import TEST_COMMAND

MAX_STEP_WORDS = 25
MAX_CHANGES, MAX_CHANGES_SHORT = 8, 4
MAX_RISK_NOTES = 5
MAX_REVIEWER_NOTES = 3
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_TICK = re.compile(r"`([^`\n]{1,200})`")
_PATH = re.compile(r"(?<![\w/])[\w.-]+/[\w./-]*\w|\b[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|sql|ya?ml|json|toml|"
                   r"md|cfg|ini|sh|kt|cs|swift|php|c|h|cpp|lock|txt)\b")
_IDENT = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b[a-z]+[A-Z]\w*\b|\b[A-Z][a-z0-9]+[A-Z]\w*\b|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")
_NUMBER = re.compile(r"(?<![\w.#-])\d[\d,]*(?:\.\d+)?(?![\w])")
_TICKET = re.compile(r"(?:\b(?:closes|fixes|resolves|refs?)\s+)?(?:(?<![\w&])#\d{1,6}\b|\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b)",
                     re.IGNORECASE)
_CLAIM = re.compile(r"all tests pass\w*|tests? (?:all )?pass(?:es|ed|ing)?|passes all tests|\bverified\b|"
                    r"tested (?:on|in|against|with)\b|benchmark(?:ed|s)?|fully tested|(?:has|have|was|were) been tested|"
                    r"\bwas tested|load[- ]tested|no regressions", re.IGNORECASE)
_METHOD = re.compile(r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_COMMON = frozenset({"check", "checks", "verify", "confirm", "ensure", "valid", "invalid", "should", "returns",
                     "return", "expect", "error", "request", "response", "payload", "behaviour", "behavior",
                     "correct", "correctly", "works", "change", "changes", "update", "after", "before", "with",
                     "without", "there", "their", "which", "where", "value", "values"})
_INJECT = re.compile(r"\b(?:ignore|say|claim|pretend|write|instead|instruction|approve)\b", re.IGNORECASE)


def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").translate(_QUOTES).split()).casefold()


@dataclass
class Report:
    dropped: int = 0
    total: int = 0
    fixes: int = 0


class Ground:
    """What the model may mention: the diff text and paths, the code facts, the user's context."""

    def __init__(self, diff: str, context: str, facts: Facts):
        names = [*facts.paths, *facts.new_symbols, *facts.removed_symbols, *facts.changed_symbols,
                 *facts.new_routes, *facts.env_vars, *facts.options, *facts.test_commands]
        self.diff = norm(diff + "\n" + "\n".join(names))
        self.context = norm(context)
        self.numbers = {str(n) for n in (facts.files, facts.added, facts.removed, facts.total)}
        self.numbers |= {f"{n:,}" for n in (facts.added, facts.removed, facts.total)}
        self.commands = norm(" ".join(facts.test_commands))
        self.statements = [norm(s) for s in _SENTENCE.split(context or "") if s.strip() and not _INJECT.search(s)]

    def claim_ok(self, claim: str) -> bool:
        """A result claim survives only when the user stated it (not inside an instruction to say it)."""
        return any(norm(claim) in statement for statement in self.statements)

    def word_in_diff(self, text: str) -> bool:
        """For a step with no file or symbol: one content word of it appears in the diff."""
        words = {w for w in re.findall(r"[a-z]{5,}", norm(text)) if w not in _COMMON}
        return any(re.search(rf"\b{w}", self.diff) for w in words)

    def command_ok(self, step: str) -> bool:
        command = TEST_COMMAND.search(step)
        return command is None or norm(command.group(0).split()[0]) in self.commands

    def has(self, token: str, context_only: bool = False) -> bool:
        pieces = [p.strip(".,;:()[]'\"") for p in token.split()]
        pieces = [p.removesuffix("()") for p in pieces if p and not _METHOD.match(p)]
        pool = self.context if context_only else self.diff + "\n" + self.context
        return bool(pieces) and all(norm(p) in pool for p in pieces)

    def number_ok(self, number: str, context_only: bool = False) -> bool:
        if not context_only and number.replace(",", "") in self.numbers or number in self.numbers:
            return True
        pool = self.context if context_only else self.diff + " " + self.context
        return bool(re.search(rf"(?<![\d]){re.escape(number.casefold())}(?![\d])", pool))


def identifiers(text: str) -> list[str]:
    ticked = _TICK.findall(text or "")
    rest = _TICK.sub(" ", text or "")
    return ticked + _PATH.findall(rest) + _IDENT.findall(rest)


def clean_text(text: str, ground: Ground, report: Report, context_only: bool = False) -> tuple[str, bool]:
    """(text kept, whether every identifier was grounded). Drops sentences with ungrounded numbers, ungrounded
    names or result claims; removes ticket references not in the input."""
    kept: list[str] = []
    ok = True
    for sentence in _SENTENCE.split((text or "").strip()):
        sentence = _strip_tickets(sentence, ground, report, context_only)
        claim = _CLAIM.search(sentence)
        bad_claim = claim and not ground.claim_ok(claim.group(0))
        bad_number = any(not ground.number_ok(n, context_only) for n in _NUMBER.findall(_TICK.sub(" ", sentence)))
        bad_name = any(not ground.has(i, context_only) for i in identifiers(sentence))
        if bad_claim or bad_number or bad_name:
            report.fixes += 1
            ok = ok and not bad_name
            continue
        if sentence.strip():
            kept.append(sentence.strip())
    return " ".join(kept), ok


def _strip_tickets(sentence: str, ground: Ground, report: Report, context_only: bool) -> str:
    def keep(match: re.Match) -> str:
        ref = re.sub(r"^(?:closes|fixes|resolves|refs?)\s+", "", match.group(0), flags=re.IGNORECASE)
        if norm(match.group(0)) != norm(ref):  # "Closes #N" is code-built from the user's words, never the model's
            report.fixes += 1
            return ref if ground.has(ref, context_only) else ""
        if ground.has(ref, context_only):
            return match.group(0)
        report.fixes += 1
        return ""
    return re.sub(r"\s{2,}", " ", _TICKET.sub(keep, sentence)).replace(" .", ".").strip()


def grounded_refs(text: str, ground: Ground) -> list[str]:
    return [i for i in identifiers(text) if ground.has(i)]


def check_draft(draft: dict, ground: Ground, facts: Facts, short: bool = False) -> tuple[dict, Report]:
    report = Report()
    out = dict(draft)
    out["summary"], _ = clean_text(draft["summary"], ground, report)
    why = draft.get("why_from_user")
    if why and ground.context:
        local = Report()
        why_text, _ = clean_text(why, ground, local, context_only=True)
        out["why_from_user"] = why_text if why_text and local.fixes == 0 else None
    else:
        out["why_from_user"] = None
    out["changes"] = _items(draft["changes"], report, lambda c: _change(c, ground, report))
    out["changes"] = out["changes"][:MAX_CHANGES_SHORT if short else MAX_CHANGES]
    out["test_steps"] = _items(draft["test_steps"], report, lambda s: _step(s, ground, report))
    fired = {s.name for s in facts.surfaces}
    out["risk_notes"] = _items(draft["risk_notes"], report, lambda n: _note(n, fired, ground, report))
    out["risk_notes"] = out["risk_notes"][:MAX_RISK_NOTES]
    out["reviewer_notes"] = _items(draft["reviewer_notes"], report,
                                   lambda s: _reviewer(s, ground, report))[:MAX_REVIEWER_NOTES]
    out["extra_sections"] = [{"heading": s["heading"], "text": clean_text(s["text"], ground, report)[0]}
                             for s in draft.get("extra_sections") or []]
    return out, report


def _items(values: list, report: Report, check) -> list:
    kept = []
    for value in values:
        report.total += 1
        result = check(value)
        if result is None:
            report.dropped += 1
        else:
            kept.append(result)
    return kept


def _change(change: dict, ground: Ground, report: Report) -> dict | None:
    files = [p for p in change["files"] if p in ground.diff or norm(p) in ground.diff]
    text, ok = clean_text(change["text"], ground, report)
    if not ok or not text or not (files or grounded_refs(text, ground)):
        return None
    return {"area": change["area"], "text": text, "files": files}


def _step(step: str, ground: Ground, report: Report) -> str | None:
    if len(step.split()) > MAX_STEP_WORDS:
        return None
    local = Report()
    text, _ = clean_text(step, ground, local)
    report.fixes += local.fixes
    ok = text and local.fixes == 0 and ground.command_ok(text)
    return text if ok and (grounded_refs(text, ground) or ground.word_in_diff(text)) else None


def _note(note: dict, fired: set[str], ground: Ground, report: Report) -> dict | None:
    if note["surface"] not in fired:
        return None
    text, ok = clean_text(note["text"], ground, report)
    return {"surface": note["surface"], "text": text} if ok and text else None


def _reviewer(note: str, ground: Ground, report: Report) -> str | None:
    text, ok = clean_text(note, ground, report)
    return text if ok and text and grounded_refs(text, ground) else None
