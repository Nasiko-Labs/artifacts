"""Pure: the document body, assembled by code from validated, grounded JSON in a fixed section order. The
model never writes a heading or chooses the order. ADR mode keeps four sections."""

from __future__ import annotations

import json
from collections.abc import Sequence

from diagram import diagram

NONE_STATED = "_None stated._"
RETENTION_TBD = "[TBD: retention?]"


def cell(text: str) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|") or "-"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join([*lines, *("| " + " | ".join(cell(c) for c in row) + " |" for row in rows)])


def decision_line(d: dict) -> str:
    because = f" (because {', '.join(d['satisfies'])})" if d["satisfies"] else ""
    label = "Given" if d.get("given") else "Proposed"
    alts = "; ".join(d["alternatives"])
    tail = f" Suggested alternatives: {alts}." if alts else ""
    return f"- **{label}: {d['choice']}**{because}. {d['rationale']}{tail}".rstrip()


def questions_block(questions: Sequence[dict]) -> str:
    if not questions:
        return "_No open questions._"
    return "\n".join(f"- **{q['id']}** {q['text']}" + (f" (blocks {', '.join(q['blocks'])})" if q["blocks"] else "")
                     for q in questions)


def requirements_table(reqs: Sequence[dict]) -> str:
    rows = [(r["id"] + (f" ({', '.join(r['aliases'])})" if r.get("aliases") else ""), r["type"], r["text"],
             f"\"{r['snippet']}\"") for r in reqs]
    return table(["id", "type", "requirement", "source"], rows)


def context_section(text: str, reqs: Sequence[dict], given: Sequence[str] = ()) -> str:
    goals = [f"{r['id']} {r['text']}" for r in reqs if r["type"] == "goal"]
    non_goals = [f"- {r['id']} {r['text']}" for r in reqs if r["type"] == "non_goal"]
    parts = [text or NONE_STATED]
    if given:
        parts.append("**Given (already decided):** " + "; ".join(given))
    if goals:
        parts.append("**Goals:** " + "; ".join(goals))
    parts.append("**Non-goals:**\n" + ("\n".join(non_goals) if non_goals else "none stated"))
    return "\n\n".join(parts)


def architecture(components: Sequence[dict]) -> str:
    rows = [(c["name"], c["responsibility"], c["owns"], ", ".join(c["depends_on"]), ", ".join(c["satisfies"]))
            for c in components]
    body = table(["component", "responsibility", "owns data", "depends on", "satisfies"], rows)
    picture = diagram(components)
    return body + (f"\n\n{picture}" if picture else "")


def contracts_section(contracts: Sequence[dict]) -> str:
    blocks = []
    for c in contracts:
        lines = [f"**{c['from']} -> {c['to']}**: {c['purpose'] or 'purpose not stated'}"]
        if c["fields"]:
            example = json.dumps({f: "[confirm]" for f in c["fields"]}, indent=2)
            lines.append(f"```json\n{example}\n```")
        if c["errors"]:
            lines.append("Errors: " + "; ".join(c["errors"]))
        if c["notes"]:
            lines.append("Notes: " + c["notes"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or NONE_STATED


def flows_section(flows: Sequence[dict], narrative: str) -> str:
    blocks = []
    for kind, title in (("main", "Main scenario"), ("failure", "Failure scenario")):
        flow = next((f for f in flows if f["kind"] == kind), None)
        if flow:
            steps = "\n".join(f"{n}. **{s['component']}**: {s['action']}" for n, s in enumerate(flow["steps"], 1))
            blocks.append(f"**{title}: {flow['name']}**\n{steps}")
    if narrative:
        blocks.append(narrative)
    return "\n\n".join(blocks) or NONE_STATED


def data_model(entities: Sequence[dict]) -> str:
    rows = [(e["name"], ", ".join(e["attributes"]), e["owner"] or "[confirm]", RETENTION_TBD) for e in entities]
    return table(["entity", "key attributes", "written by", "retention"], rows) if rows else NONE_STATED


def failure_table(modes: Sequence[dict]) -> str:
    rows = [(m["failure"], m["effect"], m["detection"], m["mitigation"]) for m in modes]
    return table(["failure", "effect", "detection", "mitigation"], rows) if rows else NONE_STATED


def criteria_section(criteria: Sequence[dict]) -> str:
    return "\n".join(f"- **Given** {c['given']} **when** {c['when']} **then** {c['then']} "
                     f"(verifies {', '.join(c['verifies'])})" for c in criteria) or NONE_STATED


def appendix_section(appendix: dict, agents: Sequence[dict]) -> str:
    kinds = {"code": "deterministic code", "single_call": "one bounded model call", "agent_loop": "agent loop"}
    steps = table(["step", "approach", "why"], [(s["step"], kinds[s["kind"]], s["reason"]) for s in appendix["steps"]])
    roster = table(["agent", "role", "tools", "permissions", "state and memory"],
                   [(a["name"], a["role"], ", ".join(a["tools"]), a["permissions"], a["state"]) for a in agents])
    parts = [steps, roster if agents else ""]
    for key, label in (("approvals", "Human approval points"), ("guardrails", "Guardrails"),
                       ("evaluation", "Evaluation plan"), ("budget", "Cost and latency budget")):
        parts.append(f"**{label}:** {appendix.get(key) or '[TBD]'}")
    return "\n\n".join(p for p in parts if p)


def document(title: str, reqs: Sequence[dict], design: dict, sections: dict, decisions: Sequence[dict],
             questions: Sequence[dict], agent: bool, given: Sequence[str] = ()) -> list[tuple[str, str]]:
    """[(heading, body)] in the fixed order; the first entry is the document title."""
    out = [(f"# Design: {title}", ""),
           ("## Context and goals", context_section(sections.get("context", ""), reqs, given)),
           ("## Requirements", requirements_table(reqs)),
           ("## Proposed architecture", architecture(design.get("components") or ())),
           ("## Contracts", contracts_section(design.get("contracts") or ())),
           ("## Data flow", flows_section(design.get("flows") or (), sections.get("flow_narrative", ""))),
           ("## Data model", data_model(design.get("entities") or ())),
           ("## Failure modes and mitigations", failure_table(sections.get("failure_modes") or ())),
           ("## Security and privacy", sections.get("security") or NONE_STATED),
           ("## Observability", sections.get("observability") or NONE_STATED),
           ("## Rollout and migration", sections.get("rollout") or NONE_STATED),
           ("## Acceptance criteria", criteria_section(sections.get("acceptance_criteria") or ())),
           ("## Alternatives considered", "\n".join(decision_line(d) for d in decisions) or NONE_STATED)]
    if agent and sections.get("agent_appendix"):
        out.append(("## Agent design appendix", appendix_section(sections["agent_appendix"],
                                                                 design.get("agents") or ())))
    out.append(("## Open questions", questions_block(questions)))
    return out


def adr(title: str, sections: dict, decisions: Sequence[dict], questions: Sequence[dict]) -> list[tuple[str, str]]:
    consequences = "\n".join(f"- {c}" for c in sections.get("adr_consequences") or ()) or NONE_STATED
    alternatives = "\n".join(decision_line(d) for d in decisions) or NONE_STATED
    return [(f"# ADR: {title}", ""), ("## Context", sections.get("adr_context") or NONE_STATED),
            ("## Decision", sections.get("adr_decision") or NONE_STATED), ("## Consequences", consequences),
            ("## Alternatives considered", alternatives + "\n\n**Open questions**\n" + questions_block(questions))]


def join(parts: Sequence[tuple[str, str]]) -> str:
    return "\n\n".join(f"{h}\n\n{b}" if b else h for h, b in parts)
