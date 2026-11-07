"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "postmortem-writer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns an incident timeline or chat log you paste (a chat thread, pager notes, a bullet timeline) into a "
    "blameless postmortem draft: summary, impact, timeline, detection, contributing factors, resolution, what "
    "went well and poorly, where you got lucky, action items with owners, and the questions the log can't "
    "answer. Every timeline entry and factor is backed by a verbatim quote checked in code; time to detect, "
    "time to mitigate and duration are computed in code; impact numbers come only from your log. It never "
    "invents an owner, a cause, a time or a number, and never blames a person. Follow-ups: only the action "
    "items, timeline only, shorter, show sources, as json, set an owner, add an action item, or a question "
    "about the log. It reads only what you paste: no pager, chat or metrics access, and it writes nothing "
    "anywhere. It does not track old action items."
)

SKILL_ID = "write-postmortem"
SKILL_NAME = "Write a blameless postmortem"
SKILL_DESCRIPTION = (
    "A blameless postmortem draft from a pasted incident log: grounded timeline, metrics computed in code, "
    "contributing factors, action items with owners, and open questions."
)
SKILL_TAGS = ["postmortem", "incident", "sre", "blameless", "timeline", "action items", "retro"]
SKILL_EXAMPLES = [
    "Write a postmortem from this chat thread: <paste the thread>",
    "Postmortem for the 11/2 checkout outage, sev2: <paste the timeline>",
    "Only the action items.",
]
