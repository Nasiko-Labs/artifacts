"""Fixed replies and lines: what the agent says when a step cannot run, and the code-fixed parts of every
message. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read the tracker or message anyone on your behalf. "
    "Send it through the Nasiko platform."
)
GATEWAY_UNREACHABLE = (
    "I couldn't reach the tool gateway, so I can't read the tracker. I won't guess from memory."
)
NO_TRACKER = (
    "No tracker tool is available to this agent, so I can't check issue hygiene and I won't guess from memory. "
    "Ask your admin to enable the tracker connector for this agent, then ask again."
)
NOTHING_FLAGGED = "Nothing flagged: every open issue I read has a project, an estimate where the team uses them, " \
                  "recent progress if it is in progress, and is younger than {old_days} days."
UNREADABLE_ANSWER = "I read the tracker but could not produce a result I can verify. Please ask again."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, then ask again."
)
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this may be incomplete."
TIMED_OUT = "This took longer than my time budget, so I stopped. Try a narrower request (one team or one project)."
HONESTY_LINE = "Fixing it in the tracker clears it from the next check. Replies here are not read by the assistant."
NO_CHAT_NOTE = "No chat tool: nothing can be sent. The messages are below as copyable text."
NO_CHAT_USER_NOTE = "No chat user matched this owner's email, so this message can only be copied."
EDIT_DECLINED = (
    "I don't edit, close or reassign issues: I only find hygiene problems and draft messages to their owners, "
    "sent after you approve each one. Fixing an issue in the tracker clears it from the next check."
)
NO_RESULT_YET = "I haven't run a hygiene check in this conversation yet. Ask for one, for example \"Hygiene check for team Platform\"."
NOTHING_PICKED = "Nothing selected, so nothing will be sent. Ask again when you want to message owners."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
NO_SEND_TOOL = "no chat tool that can send a direct message is granted to this agent"
FROM_RESULT_ONLY = "I can only answer from the last check's result, so here it is again:"
