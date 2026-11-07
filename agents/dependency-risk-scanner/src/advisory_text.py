"""Pure: the regular expressions that read an advisory paste. Each helper returns text found verbatim
in the paste (a `before 2.31.0` phrase becomes `<2.31.0`; the version itself is verbatim)."""

from __future__ import annotations

import re

_CVE = r"CVE-\d{4}-\d{4,}"
_GHSA = r"GHSA(?:-[0-9a-z]{4}){3}"
_IDS = re.compile(rf"\b(?:{_CVE}|{_GHSA})\b", re.IGNORECASE)
_ECO_WORDS = (
    ("npm", r"\bnpm\b|\bnode(?:\.js|js)?\b|\bpackage\.json\b"),
    ("pip", r"\bpip\b|\bpypi\b|\bpython\b|\brequirements\.txt\b"),
    ("go", r"\bgolang\b|\bgo modules?\b|\(go\)|\bgo\.mod\b|ecosystem:\s*go\b"),
    ("cargo", r"\bcargo\b|\bcrates\.io\b|\brust\b"),
    ("maven", r"\bmaven\b|\bgradle\b|\bjava\b"),
    ("gem", r"\brubygems\b|\bruby\b|\bgemfile\b|\(gem\)|\bgem\b"),
)
_NAME = r"(@?[A-Za-z0-9][\w.\-/:@]*)"
_PACKAGE_PATTERNS = (
    re.compile(rf"\bpackage\s*[:=]?\s+[`'\"]?{_NAME}", re.IGNORECASE),
    re.compile(rf"[`'\"]?{_NAME}[`'\"]?\s*\((?:npm|pip|pypi|go|golang|cargo|crates\.io|maven|gradle|gem|rubygems)\)",
               re.IGNORECASE),
    re.compile(rf"\b(?:affects|in|of)\s+[`'\"]?{_NAME}[`'\"]?\s+(?:versions?\s+)?(?:<|>|=|before|prior to|below|up to)",
               re.IGNORECASE),
    re.compile(rf"[`'\"]?{_NAME}[`'\"]?\s+(?:is|has been|was)\s+deprecated", re.IGNORECASE),
    re.compile(rf"\b{_NAME}\s*(?:<=?|>=?)\s*v?\d"),
)
_STOP = frozenset({"version", "versions", "affected", "affects", "before", "fixed", "in", "and", "or", "than", "the",
                   "is", "are", "from", "to", "all", "any", "a", "an", "this", "that", "it", "package", "module",
                   "npm", "pip", "pypi", "go", "cargo", "maven", "gem", "below", "of", "manifest", "lockfile"})
_VER = r"v?\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-+]*[0-9A-Za-z])?"
_CMP = rf"(?:>=|<=|==|>|<|=)\s*{_VER}"
_RUN = re.compile(rf"{_CMP}(?:\s*,?\s*{_CMP})*(?:\s*\|\|\s*{_CMP}(?:\s*,?\s*{_CMP})*)*")
_BEFORE = re.compile(rf"\b(?:before|prior to|below|lower than|older than)\s+(?:version\s+)?({_VER})", re.IGNORECASE)
_THROUGH = re.compile(rf"\b({_VER})\s+(?:and earlier|and below|or earlier|or lower)\b|\bup to (?:and including )?"
                      rf"(?:version\s+)?({_VER})", re.IGNORECASE)
_FIXED = re.compile(rf"\b(?:fixed|patched|resolved|addressed)\s+(?:in|by)\s+(?:version\s+)?`?({_VER})"
                    rf"|\bupgrade to\s+(?:version\s+)?`?({_VER})"
                    rf"|\b(?:patched|fixed|first patched) versions?\s*:?\s*`?(?:>=\s*)?({_VER})", re.IGNORECASE)
_USE = re.compile(rf"\buse\s+[`'\"]?{_NAME}[`'\"]?", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def ids_in(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(0).upper() if m.group(0).upper().startswith("CVE") else
                               "GHSA" + m.group(0)[4:] for m in _IDS.finditer(text or "")))


def ecosystem_in(text: str) -> str:
    """The one ecosystem the text names; "" when none or several."""
    found = {eco for eco, pattern in _ECO_WORDS if re.search(pattern, text or "", re.IGNORECASE)}
    return found.pop() if len(found) == 1 else ""


def package_in(text: str) -> str:
    for pattern in _PACKAGE_PATTERNS:
        for match in pattern.finditer(text or ""):
            name = match.group(1).rstrip(".,:;)`'\"")
            if name and name.casefold() not in _STOP and not _IDS.fullmatch(name) and not name[0].isdigit():
                return name
    return ""


def ranges_in(text: str) -> tuple[str, ...]:
    found = [m.group(0).strip().rstrip(",") for m in _RUN.finditer(text or "")]
    found += [f"<{m.group(1)}" for m in _BEFORE.finditer(text or "")]
    found += [f"<={m.group(1) or m.group(2)}" for m in _THROUGH.finditer(text or "")]
    return tuple(dict.fromkeys(f for f in found if f))


def fixed_in(text: str) -> tuple[str, ...]:
    found = [next(g for g in m.groups() if g) for m in _FIXED.finditer(text or "")]
    return tuple(dict.fromkeys(f.rstrip(".") for f in found))


def summary_in(text: str, ids: tuple[str, ...]) -> str:
    """The first line that says more than an id, verbatim, at most 300 characters."""
    for line in (text or "").splitlines():
        clean = line.strip()
        rest = _IDS.sub("", clean).strip(" :-/|")
        if len(rest) >= 8:
            return clean[:300]
    return ""


def replacement_in(text: str) -> str:
    match = _USE.search(text or "")
    return match.group(1).rstrip(".,;)`'\"") if match else ""


def verbatim(value: str, paste: str) -> bool:
    """`value` appears in the paste (case and whitespace runs ignored)."""
    wanted = _SPACES.sub(" ", (value or "").strip()).casefold()
    return bool(wanted) and wanted in _SPACES.sub(" ", paste or "").casefold()

