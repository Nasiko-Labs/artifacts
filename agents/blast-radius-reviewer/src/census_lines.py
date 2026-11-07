"""Pure: line-level census facts. Changed literals (classified as env default, observability name, timing,
behaviour default or a rule that may live twice), env and config keys, wire fields and status codes, SQL
operations, removed awaits/locks, dependency versions and edited test expectations."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from diff_parse import DiffFile, Line
from facts import Fact

_LITERAL = re.compile(r"\"([^\"\\\n]{1,120})\"|'([^'\\\n]{1,120})'|(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])")
_ENV_READ = re.compile(r"(?:os\.environ(?:\.get)?\s*[\[(]\s*|os\.getenv\(\s*|getenv\(\s*|env::var\(\s*|"
                       r"System\.getenv\(\s*|ENV\[\s*)[\"']([A-Z][A-Z0-9_]{2,})[\"']|process\.env\.([A-Z][A-Z0-9_]{2,})"
                       r"|process\.env\[[\"']([A-Z][A-Z0-9_]{2,})")
_ENV_DECL = re.compile(r"^\s*(?:export\s+|ENV\s+|ARG\s+|-\s*name:\s*)?([A-Z][A-Z0-9_]{2,})\s*(?:=|:|\s+\S)")
_CONFIG_KEY = re.compile(r"^\s*\"?([A-Za-z_][\w.-]{2,})\"?\s*[:=]")
_WIRE_PATH = re.compile(r"serializ|schema|dto|response|(^|/)api/|routes?|controllers?|views?|handlers?|events?|"
                        r"payloads?|graphql|proto|resources?", re.IGNORECASE)
_QUOTED_FIELD = re.compile(r"[\"']([a-z_][a-zA-Z0-9_]{2,})[\"']")
_SCHEMA_FIELD = re.compile(r"^\s*(?:(?:optional|repeated|required)\s+)?(?:[\w.<>]+\s+)?([a-z_][a-zA-Z0-9_]{2,})\s*"
                           r"(?::|=\s*\d+\s*;)")
_STATUS = re.compile(r"(?:status(?:_code)?\s*[=(:]\s*|\.status\(\s*|HTTP_)(\d{3})\b")
_OBSERVE = re.compile(r"\b(?:log(?:ger)?|logging|console)\.\w+\(|\b(?:counter|gauge|histogram|metric\w*|statsd|"
                      r"increment|emit|track)\w*\s*\(", re.IGNORECASE)
_TIMING_WORDS = re.compile(r"retr(?:y|ies)|backoff|cron|schedule|interval|topic|queue|channel|celery|beat",
                           re.IGNORECASE)
_BEHAVIOUR_WORDS = re.compile(r"timeout|limit|page_?size|per_?page|max_|min_|ttl|default|order_?by|sort|"
                              r"reverse|batch_?size|expir", re.IGNORECASE)
_FLAG_WORDS = re.compile(r"flag|feature|enabled?", re.IGNORECASE)
_CONST_DEF = re.compile(r"^\s*(?:export\s+)?(?:const\s+|final\s+|static\s+)*([A-Z][A-Z0-9_]{2,})\s*[:=]")
_FUNC = re.compile(r"(?:def|function|func|fn|fun)\s+(\w+)\s*\(")
_SYNC = re.compile(r"with\s+[\w.]*lock\b|\.acquire\(|transaction|atomic\b|\bBEGIN\b|FOR UPDATE|synchronized|mutex",
                   re.IGNORECASE)
_ASSERT = re.compile(r"assert|expect\(|should|toEqual|toBe\(|assertEqual", re.IGNORECASE)
_BOOL = re.compile(r"\b(True|False|true|false)\b")
_SECRET = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE", re.IGNORECASE)
STOPWORDS = frozenset(["about", "above", "after", "again", "against", "all", "also", "always", "and", "another", "any", "are", "because", "been", "before", "being", "below", "between", "both", "but", "can", "cannot", "could", "data", "default", "does", "done", "down", "during", "each", "else", "error", "false", "file", "from", "further", "have", "having", "here", "http", "https", "into", "just", "like", "more", "most", "must", "name", "none", "null", "other", "over", "same", "should", "some", "string", "such", "than", "that", "their", "them", "then", "there", "these", "they", "this", "those", "through", "true", "under", "until", "value", "very", "what", "when", "where", "which", "while", "will", "with", "would", "your", "yours", "text", "type", "list", "dict", "json", "true", "false", "none", "utf-8", "utf8", "index", "main", "test"])


def literals(text: str) -> list[str]:
    return [a or b or c for a, b, c in _LITERAL.findall(text)]


def skeleton(text: str) -> str:
    return " ".join(_BOOL.sub("B", _LITERAL.sub("L", text)).split())


def meaningful(token: str) -> bool:
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return sum(ch.isdigit() for ch in token) >= 2
    return len(token) >= 4 and token.casefold() not in STOPWORDS


def pairs(f: DiffFile) -> list[tuple[Line, Line]]:
    """Removed/added lines that are edits of each other: in one change block, with similar skeletons."""
    out: list[tuple[Line, Line]] = []
    for hunk in f.hunks:
        minus: list[Line] = []
        plus: list[Line] = []
        for ln in (*hunk.lines, Line(" ", "", None, None)):
            if ln.kind == "-" and not plus:
                minus.append(ln)
            elif ln.kind == "+" and minus:
                plus.append(ln)
            else:
                out += _match(minus, plus)
                minus, plus = ([ln], []) if ln.kind == "-" else ([], [])
    return out


def _match(minus: list[Line], plus: list[Line]) -> list[tuple[Line, Line]]:
    matched, used = [], set()
    for old in minus:
        best, score = None, 0.6
        for j, new in enumerate(plus):
            ratio = SequenceMatcher(None, skeleton(old.text), skeleton(new.text)).ratio()
            if j not in used and ratio > score:
                best, score = j, ratio
        if best is not None:
            used.add(best)
            matched.append((old, plus[best]))
    return matched


def enclosing_function(f: DiffFile, line: Line) -> str:
    for hunk in f.hunks:
        if line in hunk.lines:
            before = [ln.text for ln in hunk.lines[: hunk.lines.index(line)]]
            for text in reversed([hunk.header, *before]):
                m = _FUNC.search(text)
                if m:
                    return m.group(1)
    return ""


def facts_for(f: DiffFile, literals_only: bool = False) -> list[Fact]:
    facts = [fact for old, new in pairs(f) for fact in _pair_facts(f, old, new)]
    if literals_only:
        return [x for x in facts if x.kind == "literal"]
    facts += _key_facts(f)
    facts += _sync_facts(f)
    return facts


def _pair_facts(f: DiffFile, old: Line, new: Line) -> list[Fact]:
    if skeleton(old.text) != skeleton(new.text):
        removed_await = "await " in old.text and old.text.replace("await ", "", 1).strip() == new.text.strip()
        if removed_await:
            call = re.search(r"await\s+([\w.]+)", old.text)
            return [Fact("timing", f.path, new.number, call.group(1) if call else "await", "changed",
                                "`await` removed from this call", ("await",))]
        return []
    old_lits, new_lits = literals(old.text) + _BOOL.findall(old.text), literals(new.text) + _BOOL.findall(new.text)
    changed = [(a, b) for a, b in zip(old_lits, new_lits) if a != b]
    facts = []
    for a, b in changed:
        fact = _classify(f, old, new, a, b)
        if fact is not None:
            facts.append(fact)
    return facts


def _classify(f: DiffFile, old: Line, new: Line, a: str, b: str) -> Fact | None:
    text, detail = new.text, f"`{a}` changed to `{b}`"
    env = _ENV_READ.search(text)
    if env:
        name = next(g for g in env.groups() if g)
        return None if name in (a, b) else Fact("env", f.path, new.number, name, "changed",
                                                  f"default of `{name}` changed: {detail}", ("default",))
    if _OBSERVE.search(text) and meaningful(a):
        return Fact("literal", f.path, new.number, a, "changed", detail, ("observability",))
    if a in ("True", "False", "true", "false"):
        key = _CONFIG_KEY.match(text)
        if key and _FLAG_WORDS.search(key.group(1)):
            return Fact("config_key", f.path, new.number, key.group(1), "changed", f"flag {detail}", ("flag",))
        func = enclosing_function(f, new)
        return Fact("behaviour", f.path, new.number, func, "changed", detail) if func else None
    own = _FUNC.search(text)
    func = own.group(1) if own else enclosing_function(f, new)
    if _TIMING_WORDS.search(text):
        token = a if meaningful(a) else func
        return Fact("timing", f.path, new.number, token, "changed", detail, ("schedule",)) if token else None
    if _BEHAVIOUR_WORDS.search(text) or own:
        token = func or (a if meaningful(a) else "")
        return Fact("behaviour", f.path, new.number, token, "changed", detail) if token else None
    if not meaningful(a) or _CONST_DEF.match(text) or f.kind in ("docs",):
        return None  # the rule lives once, in this constant
    return Fact("literal", f.path, new.number, a, "changed", detail, ("rule",))


def _key_facts(f: DiffFile) -> list[Fact]:
    facts: list[Fact] = []
    minus, plus = f.changed("-"), f.changed("+")
    facts += _removed_keys(f, minus, plus, _env_names, "env")
    if f.kind in ("config", "ci", "build"):
        facts += _removed_keys(f, minus, plus, _config_names, "config_key")
    if f.kind == "api-schema" or _WIRE_PATH.search(f.path):
        facts += _removed_keys(f, minus, plus, _field_names, "wire")
        facts += _status_facts(f)
    return facts


def _env_names(ln: Line, kind: str) -> list[str]:
    names = [next(g for g in m.groups() if g) for m in _ENV_READ.finditer(ln.text)]
    decl = _ENV_DECL.match(ln.text) if kind in ("config", "ci", "build", "other") else None
    return names + ([decl.group(1)] if decl else [])


def _config_names(ln: Line, kind: str) -> list[str]:
    m = _CONFIG_KEY.match(ln.text)
    return [m.group(1)] if m and not m.group(1).isupper() else []


def _field_names(ln: Line, kind: str) -> list[str]:
    names = _QUOTED_FIELD.findall(ln.text)
    if kind != "source":
        m = _SCHEMA_FIELD.match(ln.text)
        names += [m.group(1)] if m else []
    return [n for n in names if n.casefold() not in STOPWORDS]


def _removed_keys(f: DiffFile, minus: list[Line], plus: list[Line], names, kind: str) -> list[Fact]:
    added = {n: ln for ln in plus for n in names(ln, f.kind)}
    facts, seen = [], set()
    for ln in minus:
        for name in names(ln, f.kind):
            if name in added or name in seen:
                continue
            seen.add(name)
            renamed = [n for n, a in added.items() if n not in {x for m in minus for x in names(m, f.kind)}
                       and a.new_no is not None and abs(a.new_no - (ln.old_no or 0)) <= 3]
            change, detail = ("renamed", f"`{name}` renamed to `{renamed[0]}`") if renamed else \
                ("removed", f"`{name}` removed")
            source = added[renamed[0]] if renamed else ln
            flags = tuple(x for x, hit in (("secret", bool(_SECRET.search(name))),
                                           ("no_fallback", _no_fallback(source.text))) if hit)
            facts.append(Fact(kind, f.path, ln.old_no, name, change, detail, flags))
    return facts


def _no_fallback(text: str) -> bool:
    if "||" in text or "??" in text:
        return False
    return bool(re.search(r"os\.environ\[|process\.env|(?:getenv|environ\.get|env::var)\(\s*[\"'][A-Z0-9_]+[\"']\s*\)",
                          text))


def _status_facts(f: DiffFile) -> list[Fact]:
    facts = []
    for old, new in pairs(f):
        a, b = _STATUS.search(old.text), _STATUS.search(new.text)
        if a and b and a.group(1) != b.group(1):
            facts.append(Fact("wire", f.path, new.number, a.group(1), "changed",
                                     f"status code `{a.group(1)}` changed to `{b.group(1)}`"))
    return facts


def _sync_facts(f: DiffFile) -> list[Fact]:
    added_text = "\n".join(ln.text for ln in f.changed("+"))
    facts = []
    for ln in f.changed("-"):
        m = _SYNC.search(ln.text)
        if m and m.group(0) not in added_text:
            token = enclosing_function(f, ln) or m.group(0)
            facts.append(Fact("timing", f.path, ln.old_no, token, "removed",
                                     f"`{m.group(0).strip()}` removed", ("lock",)))
    return facts


def test_expectation_facts(f: DiffFile) -> list[Fact]:
    facts = []
    for old, new in pairs(f):
        if _ASSERT.search(new.text) and skeleton(old.text) == skeleton(new.text):
            for a, b in zip(literals(old.text), literals(new.text)):
                if a != b and meaningful(a):
                    facts.append(Fact("test_expected", f.path, new.number, a, "changed",
                                             f"expected `{a}` edited to `{b}`"))
    return facts
