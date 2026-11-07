"""Fixed replies: what the agent says when a step cannot run. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read the tracker or the support desk on your "
    "behalf. Send it through the Nasiko platform."
)
GATEWAY_UNREACHABLE = (
    "I couldn't reach the tool gateway, so I can't check the bug or its customers. I won't guess from memory."
)
NO_TOOLS = (
    "No tracker, code host or support desk tool is available to this agent: check that those connectors are "
    "enabled for it."
)
NO_TRACKER_TOOL = (
    "No tracker tool is available to this agent, so I can't read the bug: check that the tracker connector is "
    "enabled for it. I won't guess from memory."
)
NOT_FOUND = "I couldn't find that bug in the tracker. Check the issue key and ask again."
NOTHING_SHIPPED_IN_WINDOW = (
    "I found no bugs that moved to a done or released status in the last {days} days that I can back with "
    "the tracker's own data."
)
UNREADABLE_ANSWER = "I looked, but could not produce a result I can verify. Please ask again."
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this may be incomplete."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, "
    "then ask again."
)
NO_DESK_RESULT = "No support-desk tickets could be searched, so there is nothing to reply to yet."
NOTHING_FOUND = "I found no open support tickets linked to this bug or clearly matching it."
NOTHING_SELECTED = "Nothing selected, so nothing was drafted. Ask again when you want replies drafted."
NO_LIST_YET = "I haven't checked a bug in this conversation yet. Tell me which bug shipped (for example PROJ-482)."
UNKNOWN_BUG_CHOICE = "That isn't one of the bugs I listed. Ask again with the issue key."
NO_WRITE_TOOL = "I have no granted support-desk tool that can post a reply, so I can't send it."


def not_shipped(verdict: str, reason: str) -> str:
    lead = {
        "merged_not_released": "The fix doesn't look released yet",
        "released_in_tracker_only": "The tracker says it's released, but I can't find the release on the code host",
        "unverified": "I can't confirm the fix shipped",
    }.get(verdict, "I can't confirm the fix shipped")
    return (f"{lead}: {reason}. I won't draft customer replies until a published release backs it, so nobody "
            "is told a fix is out before it is.")
