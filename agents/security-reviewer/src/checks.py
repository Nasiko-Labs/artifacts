"""Pure: the code checks every model item passes before it can become a finding.

A triage entry needs a valid verdict and (unless rejected) an impact that names a harmed party or asset;
an extra finding also needs a quote found verbatim in the masked input; `fix_code` is dropped when it
reintroduces a flagged pattern, carries a secret, or adds a network/shell call the input does not have.
No model text may call the code secure."""

from __future__ import annotations

import re

from diff_parse import CodeFile, Line
from grounding import locate, normalise, unknown_identifiers
from rules import scan_file
from secret_scan import Masker
from severity import CATEGORIES

VERDICTS = ("confirmed", "likely", "rejected", "needs_validation")
REACHABLE = ("yes", "no", "unclear")
MAX_TEXT = 400
MAX_FIX_CODE = 1_500
MIN_IMPACT_WORDS = 3
NO_FINDINGS = "no findings in the checks I run"

_HARMED = re.compile(
    r"(?i)\b(?:users?|customers?|attackers?|accounts?|admins?|data|databases?|records?|servers?|hosts?|systems?|"
    r"files?|secrets?|credentials?|tokens?|sessions?|tenants?|services?|anyone|callers?|clients?|browsers?|"
    r"payments?|money|funds|keys?|passwords?|internal|network|containers?|repositor(?:y|ies)|pipelines?|builds?|"
    r"organi[sz]ations?|employees?|orders?|documents?|resources?|infrastructure|machines?|processes|applications?|"
    r"apps?|apis?|endpoints?|visitors?|victims?|developers?|ci|production|privileges?)\b")
_SECURE_VERDICT = re.compile(
    r"(?i)\b(?:is|are|looks|appears|seems|be|remains?)\s+(?:now\s+|completely\s+|fully\s+|totally\s+|perfectly\s+)?"
    r"(?:secure|safe)\b|\bno (?:known |security )?vulnerabilit(?:y|ies)\b|\bno security (?:issues|problems|risks)\b|"
    r"\bfree (?:of|from) (?:vulnerabilities|security issues)\b")
_NET_OR_SHELL = re.compile(r"\b(?:requests\.\w+|urllib\w*|urlopen|fetch|axios|http\.(?:Get|Post)|subprocess|os\.system|"
                           r"os\.popen|child_process|exec|Runtime\.getRuntime|curl|wget|socket)\b")


def scrub(text: str) -> str:
    """Replaces a 'secure' / 'no vulnerabilities' verdict in model text."""
    def repl(match: re.Match) -> str:
        return NO_FINDINGS if match.group(0).casefold().startswith(("no ", "free")) else f"has {NO_FINDINGS}"
    return _SECURE_VERDICT.sub(repl, text or "")


def clip(value, limit: int = MAX_TEXT) -> str:
    return scrub(" ".join(str(value or "").split()))[:limit]


def names_harm(impact: str) -> bool:
    return len(impact.split()) >= MIN_IMPACT_WORDS and bool(_HARMED.search(impact))


def check_fix_code(code, norm_corpus: str) -> tuple[str | None, bool]:
    """(fix_code or None, True when a proposed fix_code was dropped)."""
    text = str(code or "").strip()
    if not text or text.casefold() in ("null", "none"):
        return None, False
    if len(text) > MAX_FIX_CODE or "<<SECRET:" in text or Masker().mask_text(text)[1]:
        return None, True
    lines = tuple(Line(n, t) for n, t in enumerate(text.split("\n"), start=1))
    if scan_file(CodeFile("fix", lines), "generic"):
        return None, True
    for call in set(_NET_OR_SHELL.findall(text)):
        if normalise(call) not in norm_corpus:
            return None, True
    return scrub(text), False


def triage_item(item, norm_corpus: str) -> tuple[dict | None, str | None, bool]:
    """(clean entry, violation, fix_code dropped) for one triage entry."""
    verdict = str((item or {}).get("verdict") or "").strip().casefold()
    if verdict not in VERDICTS:
        return None, "verdict must be confirmed, likely, rejected or needs_validation", False
    reachable = str(item.get("reachable_from_input") or "unclear").strip().casefold()
    impact = clip(item.get("impact"), 300)
    if verdict != "rejected":
        if not names_harm(impact):
            return None, "impact must name who or what is harmed", False
        missing = unknown_identifiers(impact, norm_corpus)
        if missing:
            return None, f"impact names {missing[0]!r}, which is not in the code", False
    fix_code, dropped = check_fix_code(item.get("fix_code"), norm_corpus)
    return {"verdict": verdict, "reachable_from_input": reachable if reachable in REACHABLE else "unclear",
            "impact": impact, "fix": clip(item.get("fix")), "fix_code": fix_code,
            "reason": clip(item.get("reason"))}, None, dropped


def extra_item(item, files: tuple[CodeFile, ...], norm_corpus: str) -> tuple[dict | None, str | None, bool]:
    """(located extra finding, violation, fix_code dropped) for one free-form finding."""
    if not isinstance(item, dict):
        return None, "not an object", False
    category = str(item.get("category") or "").strip().casefold()
    if category not in CATEGORIES:
        return None, f"category must be one of {', '.join(CATEGORIES)}", False
    quote = str(item.get("quote") or "")
    where = locate(quote, files, str(item.get("file") or ""))
    if where is None:
        return None, f"quote {quote[:80]!r} is not copied exactly from one line of the code", False
    issue, impact = clip(item.get("issue"), 300), clip(item.get("impact"), 300)
    if not names_harm(impact):
        return None, "impact must name who or what is harmed", False
    missing = unknown_identifiers(f"{issue} {impact}", norm_corpus)
    if missing:
        return None, f"names {missing[0]!r}, which is not in the code", False
    fix_code, dropped = check_fix_code(item.get("fix_code"), norm_corpus)
    code, line = where
    return {"category": category, "file": code.path, "line": line.no, "removed": line.kind == "del",
            "quote": line.text.strip()[:300], "issue": issue, "impact": impact, "fix": clip(item.get("fix")),
            "fix_code": fix_code, "reason": clip(item.get("reason"))}, None, dropped
