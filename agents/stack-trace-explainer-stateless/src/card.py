"""Card text (stateless variant), shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "stack-trace-explainer-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Explains a pasted error message or stack trace (Python, JavaScript/Node, Java/Kotlin, Go, Rust, "
    "Ruby, C#): the probable cause, the exact line to look at, a fix and how to confirm it. The trace is "
    "parsed in code, so the location is the innermost frame of your own code (library frames are skipped) "
    "and chained exceptions are explained root cause first. Paste the code around that line (e.g. under "
    "'# file: app/orders.py') for a high-confidence answer; without it, it says exactly which lines to "
    "paste. Every quote, path and line number is checked against what you pasted. It keeps nothing "
    "between messages: follow-up questions and code pasted later work on the error still in the recent "
    "conversation, so a very long earlier trace may need pasting again. It never runs code, reads no "
    "repository or logs, and sends nothing. Not for general programming questions, writing new code or "
    "code review."
)

SKILL_ID = "explain-stack-trace"
SKILL_NAME = "Explain a stack trace"
SKILL_DESCRIPTION = (
    "The probable cause, the line to look at, a fix and how to verify it, for a pasted error or stack trace "
    "plus optional code snippets. Follow-ups: paste the missing lines, ask why, or ask for the result as json."
)
SKILL_TAGS = ["stack trace", "error", "exception", "debugging", "traceback", "panic", "crash"]
SKILL_EXAMPLES = [
    "Explain this: <paste a Python traceback>",
    "Why does this crash? <Java stack trace> + the method code under '// file: OrderRepository.java'",
    "What does `ECONNREFUSED 127.0.0.1:5432` mean?",
]
