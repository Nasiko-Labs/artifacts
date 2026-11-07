"""Pure (plus the limited subprocess run): the regex analysis. Tokens, flags, test results, ReDoS probe and
the gotchas G01–G12, all computed in code. The model only narrates these facts."""

from __future__ import annotations

import re

import regex_probe as probe
from analysis import Analysis
from gotchas import Gotcha, ordered
from probes import Probes
from regex_tokens import (
    FLAG_WORDS,
    Token,
    capture_count,
    quantifier_after,
    split_literal,
    tokenize,
    unbounded,
)

ATTACK_REPEAT = 30
_VALIDATE = re.compile(r"\b(?:validat\w*|valid|verify|check (?:if|that|whether)|is an?|must be|only)\b",
                       re.IGNORECASE)
_ALNUM_ESC = {"\\d", "\\w"}
_CHAR_FOR = {"\\d": "1", "\\w": "a", "\\s": " ", ".": "a"}


def analyze(config: str, probes: Probes) -> Analysis:
    pattern, flags = split_literal(config.strip())
    flavour = probes.flavour or ("javascript" if flags else None)
    error = probe.compile_error(pattern, flags, flavour)
    tokens = tokenize(pattern)
    facts = {"pattern": pattern, "flags": {f: FLAG_WORDS[f] for f in flags}, "flavour": flavour or "not stated",
             "engine": "Python re", "capturing_groups": capture_count(tokens),
             "tokens": [{"text": t.text, "depth": t.depth, "means": t.desc} for t in tokens]}
    if error:
        facts["engine_error"] = error
        gotchas = ordered(static_gotchas(tokens, pattern, flags, flavour, probes.words))
        return Analysis("regex", pattern, facts, tuple(gotchas), parse_error=f"Python's engine rejects it: {error}",
                        summary=f"Regex `{pattern}`: does not compile in Python re ({error}).")
    found = static_gotchas(tokens, pattern, flags, flavour, probes.words)
    redos = _redos(tokens, pattern, flags, flavour)
    if redos is not None:
        found.append(redos)
    tests = probe.run(pattern, flags, list(probes.tests), flavour) if probes.tests else probe.RunResult()
    if tests.timed_out:
        found.append(Gotcha("G06", "high", "Running your test strings took longer than 1 second: catastrophic "
                            "backtracking, so a crafted input can hang whatever runs this pattern.", pattern))
    facts["tests"] = list(tests.results)
    checked = tuple(_test_line(r) for r in tests.results)
    summary = f"Regex `{pattern}`: {len(tokens)} tokens, {capture_count(tokens)} capturing groups"
    summary += f", {len(tests.results)} test strings run." if tests.results else "."
    return Analysis("regex", pattern, facts, tuple(ordered(found)), checked=checked, summary=summary,
                    partial=flavour is None and bool(flags))


def _test_line(result: dict) -> str:
    groups = f"; groups {result['groups']}" if result.get("groups") else ""
    return f"`{result['string']}` → {result['result']}{groups}"


def static_gotchas(tokens: list[Token], pattern: str, flags: str, flavour: str | None, words: str) -> list[Gotcha]:
    found: list[Gotcha] = []
    for index, token in enumerate(tokens):
        if token.kind == "dot" and quantifier_after(tokens, index) is None and _next_to_literal(tokens, index):
            found.append(Gotcha("G01", "medium", "An unescaped `.` matches ANY character, not just a dot: write "
                                "`\\.` for a literal dot.", _around(pattern, token.start)))
        greedy = token.kind == "dot" and unbounded(quantifier_after(tokens, index))
        if greedy and len(tokens[index + 1].text) == 1 and _literal_before_and_after(tokens, index):
            found.append(Gotcha("G03", "low", "A greedy `.*`/`.+` between two delimiters runs to the LAST "
                                "delimiter; use a lazy `.*?` or a negated class like `[^\"]*`.",
                                _around(pattern, token.start)))
        if token.kind == "class" and "A-z" in token.text:
            found.append(Gotcha("G04", "low", "`A-z` also includes `[ \\ ] ^ _` and the backtick (ASCII 91–96); "
                                "use `A-Za-z`.", token.text))
        if token.kind == "class" and "\\b" in token.text:
            found.append(Gotcha("G09", "low", "Inside a character class `\\b` means backspace, not a word "
                                "boundary.", token.text))
    anchors = [t for t in tokens if t.kind == "anchor" and t.text in ("^", "$", "\\b", "\\A", "\\Z", "\\z")]
    if not anchors and _VALIDATE.search(words or ""):
        found.append(Gotcha("G02", "medium", "No anchors (`^…$`): it also matches inside longer strings, so it "
                            "doesn't validate a whole value.", pattern))
    if _top_level_alternation(tokens) and anchors:
        found.append(Gotcha("G05", "medium", "`|` has the lowest precedence: `^a|b$` means `^a` OR `b$`. Wrap the "
                            "alternatives: `^(?:a|b)$`.", pattern))
    if pattern.endswith("$") and not pattern.endswith("\\$") and flavour != "javascript":
        found.append(Gotcha("G07", "low", "In Python, PCRE and Ruby `$` also matches before a trailing newline; "
                            "use `\\Z` (Python) or `\\z` to forbid it.", "$"))
    if any(t.text in _ALNUM_ESC for t in tokens) and flavour != "javascript":
        found.append(Gotcha("G08", "low", "In Python, Java (with UNICODE_CHARACTER_CLASS) and .NET, `\\d`/`\\w` "
                            "also match non-ASCII digits and letters; use `[0-9]` if you mean ASCII.",
                            next(t.text for t in tokens if t.text in _ALNUM_ESC)))
    if "i" not in flags and _case_pairs(tokens):
        found.append(Gotcha("G10", "low", "It spells out both cases by hand; the case-insensitive flag (`i`) is "
                            "simpler.", _case_pairs(tokens)))
    if capture_count(tokens) >= 3 and not any(t.kind == "backref" for t in tokens):
        found.append(Gotcha("G11", "info", f"{capture_count(tokens)} capturing groups and no backreference: if "
                            "you don't read the groups, `(?:…)` is cheaper.", pattern))
    found += _variable_lookbehind(tokens, pattern)
    return found


def _around(pattern: str, start: int) -> str:
    return pattern[max(0, start - 6):start + 7]


def _is_literalish(token: Token | None) -> bool:
    if token is None:
        return False
    return (token.kind == "literal" and token.text.isalnum()) or token.text in _ALNUM_ESC


def _next_to_literal(tokens: list[Token], index: int) -> bool:
    before = tokens[index - 1] if index else None
    if before is not None and before.kind == "quant" and index >= 2:
        before = tokens[index - 2]
    after = tokens[index + 1] if index + 1 < len(tokens) else None
    return _is_literalish(before) or _is_literalish(after)


def _literal_before_and_after(tokens: list[Token], index: int) -> bool:
    before = tokens[index - 1] if index else None
    after = tokens[index + 2] if index + 2 < len(tokens) else None
    return all(t is not None and t.kind in ("literal", "escape") for t in (before, after))


def _top_level_alternation(tokens: list[Token]) -> bool:
    return any(t.kind == "alt" and t.depth == 0 for t in tokens)


def _case_pairs(tokens: list[Token]) -> str:
    for token in tokens:
        body = token.text[1:-1] if token.kind == "class" else ""
        if len(body) == 2 and body[0] != body[1] and body[0].casefold() == body[1].casefold():
            return token.text
    return ""


def _variable_lookbehind(tokens: list[Token], pattern: str) -> list[Gotcha]:
    found = []
    for index, token in enumerate(tokens):
        if token.kind == "group" and token.text in ("(?<=", "(?<!"):
            end = _close_of(tokens, index)
            if any(t.kind == "quant" and not re.fullmatch(r"\{\d+\}", t.text) for t in tokens[index + 1:end]):
                found.append(Gotcha("G12", "low", "A lookbehind with a variable length is rejected by Python and "
                                    "older JavaScript engines.", pattern[token.start:token.start + 20]))
    return found


def _redos(tokens: list[Token], pattern: str, flags: str, flavour: str | None) -> Gotcha | None:
    """Static signal (a quantified group that itself contains an unbounded quantifier or an alternation),
    then a timed probe with a crafted input. A timeout confirms it."""
    for index, token in enumerate(tokens):
        if token.kind != "group" or token.text.startswith(("(?=", "(?!", "(?<=", "(?<!")):
            continue
        close = _close_of(tokens, index)
        if close >= len(tokens) or not unbounded(quantifier_after(tokens, close)):
            continue
        inner = tokens[index + 1:close]
        risky = any(unbounded(quantifier_after(tokens, i)) for i in range(index + 1, close)) or _overlapping(
            inner, token.depth + 1)
        if not risky:
            continue
        char = _attack_char(inner)
        evidence = pattern[token.start:tokens[close + 1].start + len(tokens[close + 1].text)]
        attack = probe.run(pattern, flags, [char * ATTACK_REPEAT + "!"], flavour)
        if attack.timed_out:
            return Gotcha("G06", "high", f"Catastrophic backtracking confirmed: `{char * 8}…!` "
                          f"({ATTACK_REPEAT} characters) took over 1 second. A crafted input can hang the "
                          "service running this pattern.", evidence)
        return Gotcha("G06", "medium", "A quantified group contains another quantifier or alternation: a "
                      "catastrophic-backtracking shape, though my 1-second probe finished.", evidence)
    return None


def _overlapping(inner: list[Token], depth: int) -> bool:
    """Alternatives where one is a prefix of another (`x|xx`): both can match the same text."""
    branches, current = [], ""
    for token in inner:
        if token.kind == "alt" and token.depth == depth:
            branches.append(current)
            current = ""
        else:
            current += token.text
    branches.append(current)
    return len(branches) > 1 and any(a != b and b.startswith(a) and a for a in branches for b in branches)


def _close_of(tokens: list[Token], index: int) -> int:
    depth = tokens[index].depth
    return next((i for i in range(index + 1, len(tokens)) if tokens[i].kind == "close" and tokens[i].depth == depth),
                len(tokens))


def _attack_char(inner: list[Token]) -> str:
    for token in inner:
        if token.kind == "literal" and token.text.strip():
            return token.text
        if token.text in _CHAR_FOR:
            return _CHAR_FOR[token.text]
        if token.kind == "class":
            body = token.text.lstrip("[^")
            return body[0] if body and body[0] not in "\\]" else "a"
    return "a"


MAX_EXAMPLES = 3


def verify_examples(analysis: Analysis, proposed: object) -> tuple[dict, int]:
    """The model's proposed examples, kept only when a real run behaves as claimed. Returns
    ({"should_match": [...], "should_not_match": [...]}, dropped count)."""
    data = proposed if isinstance(proposed, dict) else {}
    claims = {key: [s for s in (data.get(key) or []) if isinstance(s, str) and s][:MAX_EXAMPLES]
              for key in ("should_match", "should_not_match")}
    strings = claims["should_match"] + claims["should_not_match"]
    if not strings:
        return {"should_match": [], "should_not_match": []}, 0
    flags = "".join(analysis.facts.get("flags") or {})
    flavour = analysis.facts.get("flavour")
    run = probe.run(analysis.config, flags, strings, None if flavour == "not stated" else flavour)
    if run.timed_out or run.error:
        return {"should_match": [], "should_not_match": []}, len(strings)
    outcome = {r["string"]: r["result"] for r in run.results}
    kept = {"should_match": [s for s in claims["should_match"] if outcome.get(s[:probe.MAX_INPUT_CHARS]) not in
                             (None, "no match")],
            "should_not_match": [s for s in claims["should_not_match"] if outcome.get(s[:probe.MAX_INPUT_CHARS])
                                 == "no match"]}
    return kept, len(strings) - len(kept["should_match"]) - len(kept["should_not_match"])
