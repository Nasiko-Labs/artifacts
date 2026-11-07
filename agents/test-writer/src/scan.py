"""Pure: regex scans shared by the parsers: externals to mock, mutated arguments, keys read, comparison
literals and branch keywords (JS/Go), top-level parameter splitting and brace matching."""

from __future__ import annotations

import re

from code_model import Comparison

EXTERNALS = {
    "python": r"requests\.(?:get|post|put|patch|delete|request|Session)|httpx\.\w+|urllib\.request\.\w+|urlopen|"
              r"\bopen(?=\()|\bPath(?=\()|os\.environ|os\.getenv|datetime\.(?:now|utcnow|today)|date\.today|"
              r"time\.time|time\.sleep|random\.\w+|uuid\.uuid4|subprocess\.\w+|cursor\.\w+|session\.\w+|"
              r"sqlite3\.\w+|smtplib\.\w+|boto3\.\w+",
    "js": r"\bfetch(?=\()|axios(?:\.\w+)?|https?\.(?:get|request)|fs\.\w+|Date\.now|new Date(?=\(\))|Math\.random|"
          r"process\.env|child_process|\bexec(?=\()|\bdb\.\w+|\bsql\.\w+|localStorage\.\w+",
    "go": r"http\.(?:Get|Post|NewRequest|DefaultClient)|client\.Do|os\.(?:ReadFile|WriteFile|Open|Create|Getenv)|"
          r"ioutil\.\w+|time\.Now|rand\.\w+|sql\.Open|db\.(?:Query|QueryRow|Exec)|exec\.Command",
}
_MUTATORS = {
    "python": r"\.(?:append|extend|insert|pop|remove|clear|sort|update|setdefault|add|discard|reverse)\(|"
              r"\[[^\]\n]+\]\s*=(?!=)",
    "js": r"\.(?:push|pop|shift|unshift|splice|sort|reverse|fill)\(|(?:\.\w+|\[[^\]\n]+\])\s*=(?!=)",
    "go": r"\[[^\]\n]+\]\s*=(?!=)|\.\w+\s*=(?!=)",
}
_ORDERS = re.compile(r"\bsorted\(|\.sort\(|\bsort\.\w+|\.reverse\(|\bmin\(|\bmax\(")
_LITERAL = r"(-?\d+(?:\.\d+)?|\"[^\"\n]*\"|'[^'\n]*')"
_COMPARE = re.compile(r"([\w.\[\]()]+)\s*(<=|>=|===|!==|==|!=|<|>)\s*" + _LITERAL)
_BRANCH = re.compile(r"\b(?:if|else|switch|case|for|while|select|catch)\b")
_OPEN, _CLOSE = "([{", ")]}"


def externals(source: str, family: str) -> tuple[str, ...]:
    found = re.findall(EXTERNALS[family], source)
    return tuple(dict.fromkeys(f.strip() for f in found))


def mutated(source: str, params: list[str], family: str) -> tuple[str, ...]:
    return tuple(p for p in params if re.search(rf"\b{re.escape(p)}(?:{_MUTATORS[family]})", source))


def keys_read(source: str, params: list[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for p in params:
        keys += re.findall(rf"\b{re.escape(p)}(?:\[|\.get\()\s*[\"']([\w-]+)[\"']", source)
    return tuple(dict.fromkeys(keys))


def orders(source: str) -> bool:
    return bool(_ORDERS.search(source))


def comparisons(body: str) -> tuple[Comparison, ...]:
    found = []
    for raw, op, value in _COMPARE.findall(body):
        subject = _balance(raw)
        kind = "string" if value[0] in "\"'" else "number"
        found.append(Comparison(subject, op, value, kind))
    return tuple(dict.fromkeys(found))


def _balance(subject: str) -> str:
    """`(qty` -> `qty`, `len(s)` stays: unmatched outer parentheses from the regex are trimmed."""
    while subject.startswith("(") and subject.count("(") > subject.count(")"):
        subject = subject[1:]
    while subject.endswith(")") and subject.count(")") > subject.count("("):
        subject = subject[:-1]
    return subject


def branches(body: str) -> int:
    return len(_BRANCH.findall(body))


def skip_string(text: str, i: int) -> int:
    """Index just past the string, template or comment starting at `i` (or `i` when there is none)."""
    if text.startswith("//", i):
        end = text.find("\n", i)
        return len(text) if end == -1 else end
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end == -1 else end + 2
    quote = text[i]
    if quote not in "\"'`":
        return i
    j = i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote or (text[j] == "\n" and quote != "`"):
            return j + 1
        j += 1
    return len(text)


def match_close(text: str, start: int) -> int:
    """Index of the bracket closing the one at `start`, skipping strings and comments; -1 if unbalanced."""
    depth, i = 0, start
    while i < len(text):
        nxt = skip_string(text, i)
        if nxt != i:
            i = nxt
            continue
        ch = text[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside brackets and strings."""
    parts, depth, i, last = [], 0, 0, 0
    while i < len(text):
        nxt = skip_string(text, i)
        if nxt != i:
            i = nxt
            continue
        ch = text[i]
        if ch in _OPEN + "<":
            depth += 1
        elif ch in _CLOSE + ">" and depth:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[last:i])
            last = i + 1
        i += 1
    parts.append(text[last:])
    return [p.strip() for p in parts if p.strip()]
