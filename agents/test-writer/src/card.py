"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "test-writer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Writes unit tests with edge cases for Python, JavaScript/TypeScript or Go code you paste into the chat: a "
    "function, a class or a module. Code reads the source first and lists what is testable (parameters, "
    "boundaries of every comparison, raised errors, docstring examples, calls to the network, files, clock or "
    "environment that must be mocked); the model then writes pytest, unittest, Jest/Vitest or Go table-driven "
    "tests that claim those edge cases by id, and code verifies each claim. The reply lists the edge cases "
    "covered and not covered, what was mocked, and marks every expected value the model worked out itself. "
    "It never changes or runs your code and never claims the tests pass. Follow-ups: use unittest, more edge "
    "cases for X, add tests for another function, as json. Not for code review, explaining code, writing the "
    "code itself, end-to-end tests or other languages."
)

SKILL_ID = "write-unit-tests"
SKILL_NAME = "Write unit tests with edge cases"
SKILL_DESCRIPTION = (
    "Unit tests for pasted Python, JS/TS or Go code, covering an edge-case list computed from the source "
    "(boundaries, raises, doctests, mocks), with what is and isn't covered. Draft to copy: nothing is run."
)
SKILL_TAGS = ["unit tests", "testing", "edge cases", "pytest", "jest", "vitest", "go test", "TDD"]
SKILL_EXAMPLES = [
    "Write pytest tests for this function: <paste the code>",
    "Jest tests for `parseAmount` and mock `fetch`: <paste the code>",
    "Go table-driven tests for this package function: <paste the code>",
]
