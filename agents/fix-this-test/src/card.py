"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "fix-this-test"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Fixes one failing Python (pytest) test. Paste the pytest failure output and the test and source files "
    "(each starting with a line like `# file: src/money.py`). It reproduces the failure by running your pasted "
    "code with pytest in a locked-down sandbox inside its own container (no credentials, CPU, memory and time "
    "limits; outbound network is not blocked by the agent), lets the model read, edit and re-run in a short "
    "bounded loop, and returns a unified diff computed by code plus whether the fix was verified by a real "
    "re-run. It fixes the code, not the test: edits that weaken a test (removing asserts, skipping, changing "
    "expected values) are refused. It never says a fix works unless the re-run passed, and it writes nothing "
    "outside its own temporary run directory; you apply the patch yourself. Python and pytest only, standard "
    "library plus pytest. Not for writing features or new tests, code review, other languages or general "
    "programming questions."
)

SKILL_ID = "fix-failing-test"
SKILL_NAME = "Fix a failing test"
SKILL_DESCRIPTION = (
    "From pasted pytest failure output and the test and source files: reproduce the failure in a sandbox, "
    "make the smallest source change that fixes the cause, re-run the tests and return a verified unified diff. "
    "Test-weakening edits are refused; nothing is written outside a temporary run directory."
)
SKILL_TAGS = ["python", "pytest", "failing test", "bug fix", "patch", "diff", "debugging"]
SKILL_EXAMPLES = [
    "Fix this failing test: <pytest output> + `# file: tests/test_money.py` ... + `# file: src/money.py` ...",
    "Why does this test fail? (no edits)",
    "Try a different fix",
]
