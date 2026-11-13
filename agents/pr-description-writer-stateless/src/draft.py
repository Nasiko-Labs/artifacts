"""The model calls that write the draft: a diff digest (map-reduce over file groups when the diff is large),
then one DRAFT call over the digest, the code facts, the user's context and the template headings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import llm
import prompts
from diff_parse import FileDiff, ParsedDiff
from facts import Facts
from llm import TurnDeps
from template import Heading

DIFF_MAP_THRESHOLD = 20_000
GROUP_CHARS = 12_000
MAX_MAP_CALLS = 3
LINES_PER_FILE = 60
MAX_LINE_CHARS = 300
LIST_FIELDS = ("test_steps", "reviewer_notes")


@dataclass(frozen=True)
class Digest:
    text: str
    grouped: bool = False
    skipped: tuple[str, ...] = ()


def _changed_lines(f: FileDiff) -> list[str]:
    body = [line for line in f.text if line[:1] in "+-" and not line.startswith(("+++ ", "--- "))]
    return [line[:MAX_LINE_CHARS] for line in body]


def file_block(f: FileDiff, kind: str, lines: int = LINES_PER_FILE) -> str:
    head = f"FILE {f.path} ({f.status}, {kind}, +{f.added} -{f.removed})"
    if f.old_path and f.old_path != f.path and f.status == "renamed":
        head += f" renamed from {f.old_path}"
    if kind == "generated" or f.binary:
        return head + " [generated or binary: summarised by name only]"
    changed = _changed_lines(f)
    more = f"\n[... {len(changed) - lines} more changed lines]" if len(changed) > lines else ""
    return head + ("\n" + "\n".join(changed[:lines]) if changed else "") + more


def _groups(files: list[FileDiff]) -> list[list[FileDiff]]:
    groups: list[list[FileDiff]] = []
    size = GROUP_CHARS + 1
    for f in files:
        length = min(len("\n".join(f.text)), GROUP_CHARS)
        if size + length > GROUP_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(f)
        size += length
    return groups


async def digest(deps: TurnDeps, parsed: ParsedDiff, facts: Facts, policy: tuple[str, ...]) -> Digest:
    if len(parsed.raw) <= DIFF_MAP_THRESHOLD:
        return Digest("\n\n".join(file_block(f, facts.kind(f.path)) for f in parsed.files))
    plain = [f for f in parsed.files if facts.kind(f.path) != "generated" and not f.binary]
    named = [f for f in parsed.files if f not in plain]
    groups = _groups(plain)
    summaries: dict[str, str] = {}
    for group in groups[:MAX_MAP_CALLS]:
        summaries.update(await _map(deps, group, facts, policy))
    skipped = tuple(f.path for group in groups[MAX_MAP_CALLS:] for f in group)
    blocks = [f"FILE {f.path} ({f.status}, {facts.kind(f.path)}, +{f.added} -{f.removed}): "
              f"{summaries.get(f.path, '[not summarised]')}" for f in plain]
    blocks += [file_block(f, facts.kind(f.path)) for f in named]
    return Digest("\n".join(blocks), True, skipped)


async def _map(deps: TurnDeps, group: list[FileDiff], facts: Facts, policy: tuple[str, ...]) -> dict[str, str]:
    paths = {f.path for f in group}
    body = "\n\n".join(file_block(f, facts.kind(f.path), lines=10_000)[:GROUP_CHARS] for f in group)
    messages = [{"role": "system", "content": prompts.MAP_SYSTEM + prompts.org_block(policy)},
                {"role": "user", "content": "INPUT (part of a diff):\n" + prompts.fence(body)}]

    def valid(data: Any) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("file_summaries"), list):
            return None
        return {"items": [s for s in data["file_summaries"] if isinstance(s, dict) and s.get("file") in paths
                          and isinstance(s.get("summary"), str)]}

    result = await llm.complete_json(deps, messages, "file_summaries", valid)
    return {s["file"]: s["summary"].strip() for s in result["items"]}


def facts_block(facts: Facts, headings: tuple[Heading, ...], conventional: bool, short: bool,
                code_steps: list[str]) -> str:
    def listed(values) -> str:
        return ", ".join(values) if values else "none"
    surfaces = "; ".join(f"{s.name} ({s.token})" for s in facts.surfaces) or "none"
    lines = [
        "FACTS (computed in code, exact; never compute):",
        f"- files changed: {facts.files}; lines added: {facts.added}; lines removed: {facts.removed}",
        f"- change type: {facts.change_type or 'not decided by code: choose one'}",
        f"- scope: {facts.scope or 'none'}",
        f"- risk level: {facts.risk}",
        f"- RISK SURFACES: {surfaces}",
        f"- test files changed: {listed(facts.test_files)}",
        f"- new public symbols: {listed(facts.new_symbols)}; new routes: {listed(facts.new_routes)}",
        f"- removed public symbols or routes: {listed(facts.removed_symbols)}",
        f"- changed signatures: {listed(facts.changed_symbols)}",
        f"- env vars / options added: {listed(facts.env_vars + facts.options)}",
        f"- ticket references in the input: {listed(facts.tickets)}",
        f"- template headings: {listed([h.title for h in headings])}",
        f"- style: {'conventional' if conventional else 'plain'} title; {'short' if short else 'normal'} length",
        "CODE STEPS (already in the test plan): " + (" | ".join(code_steps) or "none"),
    ]
    if facts.stat_only:
        lines.append("- the input is a --stat listing only: no changed lines are available")
    return "\n".join(lines)


def validate(data: Any) -> dict | None:
    """The model's draft normalised to the contract, {"status": "off_topic"}, or None when unusable."""
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic"}
    subject, summary = data.get("subject"), data.get("summary")
    if not isinstance(subject, str) or not subject.strip() or not isinstance(summary, str) or not summary.strip():
        return None
    out = {"status": "ok", "type": str(data.get("type") or ""), "subject": subject.strip(),
           "summary": summary.strip(), "why_from_user": data.get("why_from_user")
           if isinstance(data.get("why_from_user"), str) and data["why_from_user"].strip() else None}
    out["changes"] = [{"area": str(c.get("area") or "").strip(), "text": c["text"].strip(),
                       "files": [str(p) for p in c.get("files") or [] if isinstance(p, str)]}
                      for c in data.get("changes") or [] if isinstance(c, dict) and isinstance(c.get("text"), str)]
    for name in LIST_FIELDS:
        out[name] = [s.strip() for s in data.get(name) or [] if isinstance(s, str) and s.strip()]
    out["risk_notes"] = [{"surface": str(n.get("surface") or ""), "text": n["text"].strip()}
                         for n in data.get("risk_notes") or [] if isinstance(n, dict) and isinstance(n.get("text"), str)]
    out["extra_sections"] = [{"heading": str(s.get("heading") or "").strip()[:60], "text": s["text"].strip()}
                             for s in data.get("extra_sections") or []
                             if isinstance(s, dict) and isinstance(s.get("text"), str) and s.get("heading")][:2]
    return out


async def write(deps: TurnDeps, digest_text: str, facts_text: str, context: str,
                policy: tuple[str, ...]) -> dict:
    context_part = prompts.fence(context, "CONTEXT") if context else "(none: the user gave no reason)"
    messages = [{"role": "system", "content": prompts.DRAFT_SYSTEM + prompts.org_block(policy)},
                {"role": "user", "content": f"{facts_text}\n\nCONTEXT:\n{context_part}\n\nINPUT (the diff):\n"
                                            f"{prompts.fence(digest_text)}"}]
    return await llm.complete_json(deps, messages, prompts.SCHEMA, validate)
