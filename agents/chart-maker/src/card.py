"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "chart-maker"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Turns data you paste into a chart, or a process or structure you describe into a diagram, in a form "
    "the chat can show. Charts come back as a text bar chart you can see inline, a table, and a "
    "self-contained SVG picture you download as an html file and open in a browser. Diagrams come back as "
    "Mermaid source plus a plain-text outline. Every number is parsed from your data in code: it never "
    "invents, fetches or edits data, and it writes nowhere. Change it with \"line chart\", \"top 5\", "
    "\"sort by name\" or \"add a rollback step\". Not for explaining or forecasting data, statistics, "
    "general questions or drawing logos and illustrations."
)

SKILL_ID = "make-chart-or-diagram"
SKILL_NAME = "Make a chart or diagram"
SKILL_DESCRIPTION = (
    "A chart from pasted data (CSV, TSV, a markdown table, label: value lines or JSON) or a diagram of a "
    "process, sequence, hierarchy, timeline or relationships you describe. Charts: inline text chart, "
    "table and a downloadable SVG; diagrams: Mermaid source and an outline. Numbers come only from your data."
)
SKILL_TAGS = ["chart", "diagram", "visualization", "mermaid", "svg", "bar chart", "flowchart"]
SKILL_EXAMPLES = [
    "Chart this: Q1,120 / Q2,150 / Q3,90 / Q4,210",
    "Bar chart of signups by channel: <paste CSV>",
    "Diagram our onboarding: signup, verify email, choose plan, pay, welcome",
]
