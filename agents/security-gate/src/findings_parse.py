"""Pure: findings from the scanner's JSON, and from pasted findings (scanner JSON, SARIF, or this agent's own
envelope), all into the same `Finding` record. Every path must be one that was scanned and every line must
lie inside its file; anything else is dropped and counted. Severity comes from the rule metadata."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from typing import Any

from records import Finding

_UNKNOWN = "medium"
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_CWE = re.compile(r"CWE-\d+", re.IGNORECASE)


def severity_of(raw: str, confidence: str = "") -> tuple[str, bool]:
    """(critical|high|medium|low, known?). ERROR+HIGH confidence = critical, ERROR = high, WARNING = medium,
    INFO = low; third-party words (critical/high/error/warning/note ...) map the same way."""
    word, conf = (raw or "").strip().casefold(), (confidence or "").strip().casefold()
    if word in ("error", "err"):
        return ("critical" if conf == "high" else "high"), True
    table = {"critical": "critical", "blocker": "critical", "high": "high", "warning": "medium", "warn": "medium",
             "medium": "medium", "moderate": "medium", "info": "low", "note": "low", "low": "low", "none": "low",
             "inventory": "low", "experiment": "low"}
    return (table[word], True) if word in table else (_UNKNOWN, False)


def cwe_of(metadata: Any) -> str:
    raw = metadata.get("cwe") if isinstance(metadata, Mapping) else None
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        m = _CWE.search(str(value or ""))
        if m:
            return m.group(0).upper()
    return ""


def rule_name(check_id: str) -> str:
    """`app.rules.python-sql-string-concat` → `python-sql-string-concat` (bundled ids hold no dots)."""
    return (check_id or "").rsplit(".", 1)[-1]


def from_scanner(data: Any, contents: Mapping[str, str]) -> tuple[tuple[Finding, ...], int]:
    """(findings, dropped) from the scanner's `--json` output; paths relative to the scan root."""
    results = data.get("results") if isinstance(data, Mapping) else None
    out, dropped = [], 0
    for r in results if isinstance(results, list) else ():
        finding = _scanner_one(r, contents)
        if finding is None:
            dropped += 1
        else:
            out.append(finding)
    return tuple(out), dropped


def _scanner_one(r: Any, contents: Mapping[str, str]) -> Finding | None:
    if not isinstance(r, Mapping):
        return None
    path = str(r.get("path") or "").removeprefix("./")
    start, end = _line(r.get("start")), _line(r.get("end"))
    if path not in contents or not _within(contents[path], start, end):
        return None
    extra = r.get("extra") if isinstance(r.get("extra"), Mapping) else {}
    metadata = extra.get("metadata") if isinstance(extra.get("metadata"), Mapping) else {}
    confidence = str(metadata.get("confidence") or "MEDIUM").upper()
    severity, _ = severity_of(str(extra.get("severity") or ""), confidence)
    rule = rule_name(str(r.get("check_id") or ""))
    return Finding(file=path, start_line=start, end_line=max(end, start), rule_id=rule, severity=severity,
                   message=str(extra.get("message") or "").strip(), confidence=confidence, cwe=cwe_of(metadata),
                   is_secret=rule.startswith("secret"))


def _line(pos: Any) -> int:
    try:
        return int(pos.get("line")) if isinstance(pos, Mapping) else 0
    except (TypeError, ValueError):
        return 0


def _within(content: str, start: int, end: int) -> bool:
    total = len(content.splitlines())
    return 1 <= start <= total and start <= max(end, start) <= total + 1


# ---- pasted findings ---------------------------------------------------------------------------------

def pasted_object(text: str) -> dict | None:
    """The first JSON object in the message that looks like findings (scanner JSON, SARIF, an envelope)."""
    body = (text or "").strip()
    candidates = [m.group(1) for m in _FENCED.finditer(body)]
    if "{" in body:
        candidates.append(body[body.find("{"): body.rfind("}") + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and kind_of(data):
            return data
    return None


def kind_of(data: Mapping[str, Any]) -> str:
    if isinstance(data.get("runs"), list):
        return "sarif"
    results = data.get("results")
    if isinstance(results, list) and any(isinstance(r, Mapping) and "check_id" in r for r in results):
        return "scanner"
    found = data.get("findings")
    if isinstance(found, list) and any(isinstance(f, Mapping) and "rule_id" in f for f in found):
        return "envelope"
    return ""


def from_pasted(data: Mapping[str, Any]) -> tuple[tuple[Finding, ...], int, int]:
    """(findings, dropped, unknown-severity count). No file contents exist, so lines are taken as given."""
    kind = kind_of(data)
    raw = list(_sarif(data) if kind == "sarif" else _scanner_pasted(data) if kind == "scanner" else _envelope(data))
    kept = [f for f in raw if f is not None]
    unknown = sum(1 for f in kept if f.confidence == "?")
    kept = [f.with_(confidence="MEDIUM") if f.confidence == "?" else f for f in kept]
    return (tuple(f.with_(source="pasted", scope=f.scope if kind == "envelope" else "new") for f in kept),
            len(raw) - len(kept), unknown)


def _scanner_pasted(data: Mapping[str, Any]) -> Iterator[Finding | None]:
    for r in data.get("results") or ():
        if not isinstance(r, Mapping) or not r.get("path"):
            yield None
            continue
        extra = r.get("extra") if isinstance(r.get("extra"), Mapping) else {}
        metadata = extra.get("metadata") if isinstance(extra.get("metadata"), Mapping) else {}
        confidence = str(metadata.get("confidence") or "MEDIUM").upper()
        severity, known = severity_of(str(extra.get("severity") or ""), confidence)
        start, end = _line(r.get("start")), _line(r.get("end"))
        lines = str(extra.get("lines") or "")
        yield Finding(file=str(r["path"]).removeprefix("./"), start_line=start, end_line=max(end, start),
                      rule_id=rule_name(str(r.get("check_id") or "")), severity=severity,
                      message=str(extra.get("message") or "").strip(), confidence=confidence if known else "?",
                      cwe=cwe_of(metadata), snippet="" if lines == "requires login" else lines)


def _sarif(data: Mapping[str, Any]) -> Iterator[Finding | None]:
    for run in data.get("runs") or ():
        if not isinstance(run, Mapping):
            continue
        rules = _sarif_rules(run)
        for r in run.get("results") or ():
            yield _sarif_one(r, rules)


def _sarif_rules(run: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    driver = (run.get("tool") or {}).get("driver") if isinstance(run.get("tool"), Mapping) else {}
    rules = driver.get("rules") if isinstance(driver, Mapping) else None
    return {str(x.get("id")): x for x in rules or () if isinstance(x, Mapping)}


def _sarif_one(r: Any, rules: Mapping[str, Mapping[str, Any]]) -> Finding | None:
    if not isinstance(r, Mapping):
        return None
    locations = r.get("locations") or [{}]
    physical = locations[0].get("physicalLocation") if isinstance(locations[0], Mapping) else None
    if not isinstance(physical, Mapping):
        return None
    uri = str((physical.get("artifactLocation") or {}).get("uri") or "")
    region = physical.get("region") if isinstance(physical.get("region"), Mapping) else {}
    if not uri:
        return None
    rule_id = str(r.get("ruleId") or "")
    rule = rules.get(rule_id, {})
    props = rule.get("properties") if isinstance(rule.get("properties"), Mapping) else {}
    level = str(r.get("level") or (rule.get("defaultConfiguration") or {}).get("level") or "")
    severity, known = severity_of(level, str(props.get("precision") or ""))
    start = int(region.get("startLine") or 0)
    tags = props.get("tags") if isinstance(props.get("tags"), list) else []
    message = r.get("message") if isinstance(r.get("message"), Mapping) else {}
    snippet = region.get("snippet") if isinstance(region.get("snippet"), Mapping) else {}
    return Finding(file=uri.removeprefix("./"), start_line=start, end_line=int(region.get("endLine") or start),
                   rule_id=rule_name(rule_id) or "unnamed-rule", severity=severity,
                   message=str(message.get("text") or "").strip(), confidence="MEDIUM" if known else "?",
                   cwe=cwe_of({"cwe": tags}), snippet=str(snippet.get("text") or ""))


def _envelope(data: Mapping[str, Any]) -> Iterator[Finding | None]:
    for raw in data.get("findings") or ():
        yield Finding.from_dict(raw)
