"""The Spec axis: requirements split in code (R1..Rn), one JSON call over a per-file change digest, and every
evidence quote verified against the added lines. `covered` without a verified quote becomes `unclear`."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

import prompts
from checks import norm
from diff_parse import FileDiff
from inputs import fence_text
from llm import LlmError, TurnDeps, ask_json

log = logging.getLogger("code-reviewer.spec")

MAX_REQUIREMENTS = 12
DIGEST_LINES_PER_FILE = 40
DIGEST_CHARS = 12_000
MIN_EVIDENCE_CHARS = 6
MAX_NOTE_CHARS = 140
STATUSES = ("covered", "partial", "missing", "unclear")

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|R\d+[.:)]?|\[[ x]\])\s+(.*\S)\s*$", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_LIST_HEAD = re.compile(r"^(.*?\b(?:must|should|shall|needs to|need to|has to|have to)(?: be)?)\s+(.+)$", re.IGNORECASE)
_LIST_SPLIT = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+")
MAX_LIST_ITEM_WORDS = 6


def requirements(spec: str) -> list[str]:
    """Bullets or numbered lines; else sentences; a single "X must be a, b and c" becomes three."""
    lines = [ln for ln in (spec or "").split("\n") if ln.strip()]
    bullets = [m.group(1) for m in (_BULLET.match(ln) for ln in lines) if m]
    items = bullets or [s.strip() for s in _SENTENCE.split(" ".join(lines)) if s.strip()]
    if len(items) == 1:
        items = _split_list(items[0])
    return [i.rstrip(".;") for i in items][:MAX_REQUIREMENTS]


def _split_list(sentence: str) -> list[str]:
    head = _LIST_HEAD.match(sentence.rstrip(".;"))
    if not head:
        return [sentence]
    parts = [p.strip() for p in _LIST_SPLIT.split(head.group(2)) if p.strip()]
    if len(parts) < 2 or any(len(p.split()) > MAX_LIST_ITEM_WORDS for p in parts):
        return [sentence]
    return [f"{head.group(1)} {p}" for p in parts]


def digest(files: Sequence[FileDiff]) -> str:
    out: list[str] = []
    used = 0
    for f in files:
        rows = [f"### file: {f.path}"] + [f"+ NEW:{n} | {t}" for n, t in list(f.added.items())[:DIGEST_LINES_PER_FILE]]
        block = "\n".join(rows)
        if used + len(block) > DIGEST_CHARS:
            break
        out.append(block)
        used += len(block) + 2
    return "\n\n".join(out)


def verified(evidence: object, files: Sequence[FileDiff]) -> list[str]:
    added = [norm(t) for f in files for t in f.added.values()]
    out = []
    for quote in evidence if isinstance(evidence, list) else []:
        q = norm(str(quote))
        if len(q) >= MIN_EVIDENCE_CHARS and any(q in line or line == q for line in added if line):
            out.append(str(quote).strip())
    return out


def resolve(answer: dict | None, reqs: Sequence[str], files: Sequence[FileDiff]) -> dict:
    by_id = {}
    for r in (answer or {}).get("requirements") or []:
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            by_id[r["id"].strip().upper()] = r
    out = []
    for n, text in enumerate(reqs, start=1):
        rid = f"R{n}"
        r = by_id.get(rid, {})
        status = str(r.get("status") or "unclear").casefold()
        status = status if status in STATUSES else "unclear"
        evidence = verified(r.get("evidence"), files) if status != "missing" else []
        if status == "covered" and not evidence:
            status = "unclear"
        note = str(r.get("note") or "").strip()[:MAX_NOTE_CHARS] if status in ("partial", "missing") else ""
        out.append({"id": rid, "text": text, "status": status, "evidence": evidence, "note": note})
    return {"requirements": out}


async def check_spec(deps: TurnDeps, spec_text: str, files: Sequence[FileDiff], policy: Sequence[str]) -> dict | None:
    reqs = requirements(spec_text)
    if not reqs:
        return None
    numbered = "\n".join(f"R{n}: {r}" for n, r in enumerate(reqs, start=1))
    user = "\n".join(["Requirements:", fence_text("SPEC", numbered), "Change digest (added lines only):",
                      fence_text("INPUT", digest(files))])
    try:
        answer = await ask_json(deps, prompts.messages(prompts.SPEC_SYSTEM, user, policy))
    except LlmError:
        log.warning("spec call failed", exc_info=True)
        answer = None
    if answer is not None and answer.get("status") == "off_topic":
        answer = None
    return resolve(answer, reqs, files)
