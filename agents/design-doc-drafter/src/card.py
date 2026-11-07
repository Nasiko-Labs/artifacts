"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "design-doc-drafter"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns requirements you paste into the chat into a software design document with a fixed structure: context "
    "and goals, a requirements table with ids (R1, R2...), components, contracts, data flow, data model, failure "
    "modes, security, observability, rollout, acceptance criteria per requirement, alternatives and open "
    "questions. Requirement ids, vague-word detection, coverage and section order are computed in code; the "
    "model proposes the design. It never invents numbers: an unknown scale, latency or budget becomes a visible "
    "[TBD] and an open question, and technologies you didn't name are labelled \"Proposed\" with the "
    "requirement they serve. Answer an open question (\"Q2: 500 requests/second\") and only the affected "
    "sections update. ADR style on request; an agent-design appendix for LLM or agent systems. It drafts only "
    "and writes nothing anywhere. Not for writing or reviewing code, project plans or general questions."
)

SKILL_ID = "design-doc"
SKILL_NAME = "Draft a design document"
SKILL_DESCRIPTION = (
    "A design document from pasted requirements: ids, components, contracts, data flow, failure modes, "
    "acceptance criteria and open questions, with unknown numbers marked [TBD]. Draft only."
)
SKILL_TAGS = ["design doc", "architecture", "requirements", "ADR", "RFC", "acceptance criteria", "system design"]
SKILL_EXAMPLES = [
    "Draft a design doc for these requirements: <paste the requirements>",
    "ADR for choosing a queue given these constraints: <paste the constraints>",
    "Q2: 500 requests/second; Q5: no PII",
]
