"""Pure: the report's markdown, its evidence view and the envelope, all rendered by code. Numbers come from
history.py; the model's only contribution is a headline that is checked against the facts before use."""

from __future__ import annotations

import re

from history import Flaky
from scan import Scan

TOP = 10
_CODE_SPAN = re.compile(r"`([^`]+)`")
_URL = re.compile(r"https?://\S+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_COMMAND = re.compile(r"\b(?:curl|wget|bash|sudo|rm|eval|exec|sh)\b|\||\$\(")


def _dates(scan: Scan) -> str:
    days = sorted(r.created_at[:10] for r in scan.runs)
    return f"{days[0]} to {days[-1]}" if days else "no dates"


def header(scan: Scan) -> str:
    if scan.pasted:
        return (f"Flaky tests · pasted logs · {len(scan.runs)} runs ({_dates(scan)}), "
                f"{scan.inspected} failed run attempts read")
    return (f"Flaky tests · {scan.params.repo} · workflow {scan.workflow} · {scan.branch} · last {len(scan.runs)} "
            f"runs ({_dates(scan)}), {scan.inspected} failed run attempts inspected")


def _quote(line: str) -> str:
    return "`" + line.replace("`", "'") + "`" if line else "(no log line quoted)"


def flaky_line(f: Flaky) -> str:
    signals = []
    if f.rerun_flip:
        signals.append(f"passed on rerun of {f.rerun_flip} commit{'s' if f.rerun_flip != 1 else ''}")
    if f.main_flip:
        tag = " (unverified: commit history not checked)" if f.unverified else ""
        signals.append(f"{f.main_flip} isolated failure{'s' if f.main_flip != 1 else ''} between green runs{tag}")
    return (f"{f.rank}. {f.test}  failed {f.k} of {f.n} (at least {f.rate_pct}%) · " + " · ".join(signals)
            + f" · score {f.score}")


def _first_evidence(f: Flaky) -> str:
    if not f.evidence:
        return ""
    e = f.evidence[0]
    return f"   evidence: run {e.run} ({e.sha}, {e.attempt}) > {_quote(e.line)}"


def coverage(scan: Scan) -> list[str]:
    lines = list(scan.notes)
    if scan.not_inspected:
        lines.append(f"{scan.not_inspected} failed run attempts not inspected (limit {scan.inspected}); "
                     "rates are lower bounds.")
    if scan.unattributed:
        lines.append(f"{scan.unattributed} failed run attempts had no readable test failure (log unreadable, "
                     "or the failure was not a test).")
    if len(scan.runs) < scan.params.runs and not scan.pasted:
        lines.append(f"Only {len(scan.runs)} completed runs in the window (asked for {scan.params.runs}).")
    return lines


def report(scan: Scan, headline: str) -> str:
    out = [header(scan)]
    if headline:
        out += ["", headline]
    flaky = scan.ranking.flaky
    if not scan.failed and not scan.pasted:
        out += ["", f"No failed runs in the last {len(scan.runs)}; nothing flaky."]
    elif not flaky:
        out += ["", "No test looks flaky: no failure passed on a rerun or sat alone between green runs."]
    out.append("")
    for f in flaky[:TOP]:
        out += [flaky_line(f), _first_evidence(f)]
    if len(flaky) > TOP:
        out.append(f"... and {len(flaky) - TOP} more (say \"show evidence for #{TOP + 1}\").")
    for b in scan.ranking.broken:
        out.append(f"Broken, not flaky: {b.test} (failed in last {b.failed} of {b.failed} runs since {b.since_sha})")
    notes = coverage(scan)
    if notes:
        out += ["", "Coverage: " + " ".join(notes)]
    if flaky:
        out += ["", 'Say "show evidence for #2" or "draft the PR for #1".']
    return "\n".join(line for line in out if line is not None).strip()


def evidence(f: Flaky) -> str:
    out = [flaky_line(f), f"File: {f.file or 'unknown (found by code search when drafting)'}"]
    for e in f.evidence:
        link = f" · {e.url}" if e.url else ""
        out.append(f"- run {e.run} · {e.sha} · {e.date} · {e.attempt}{link}\n  > {_quote(e.line)}")
    return "\n".join(out)


def confidence(scan: Scan) -> str:
    if scan.pasted or len(scan.runs) < 10:
        return "low"
    gaps = scan.not_inspected or scan.unattributed or any(f.unverified for f in scan.ranking.flaky[:3])
    return "medium" if gaps or len(scan.runs) < 20 else "high"


def needs_human(scan: Scan) -> bool:
    top = scan.ranking.flaky[:3]
    return any(f.unverified and not f.rerun_flip for f in top) or confidence(scan) == "low"


def envelope(scan: Scan, summary: str, draft_pr: dict | None = None) -> dict:
    flaky = [f.to_dict() for f in scan.ranking.flaky]
    return {"summary": summary, "items": flaky, "confidence": confidence(scan), "needs_human": needs_human(scan),
            "flaky": flaky, "broken": [{"test": b.test, "failed": b.failed, "since": b.since_sha}
                                       for b in scan.ranking.broken],
            "coverage": coverage(scan), "draft_pr": draft_pr}


def facts(scan: Scan) -> str:
    lines = [header(scan)]
    lines += [flaky_line(f) for f in scan.ranking.flaky[:5]]
    lines += [f"Broken, not flaky: {b.test}, failed in {b.failed} runs" for b in scan.ranking.broken]
    return "\n".join(lines)


def code_headline(scan: Scan) -> str:
    n = len(scan.ranking.flaky)
    if not n:
        return ""
    top = scan.ranking.flaky[0]
    return f"{n} flaky test{'s' if n != 1 else ''} found; the flakiest is {top.test} (failed {top.k} of {top.n} runs)."


def checked_headline(text: str, facts_text: str) -> bool:
    """Every number, code span and link in the model's headline must appear in the facts."""
    if not text or len(text) > 400 or "\n" in text.strip():
        return False
    tokens = [*_NUMBER.findall(text), *_CODE_SPAN.findall(text), *_URL.findall(text), *_COMMAND.findall(text)]
    return all(t in facts_text for t in tokens)
