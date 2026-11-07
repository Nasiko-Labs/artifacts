"""Fixed replies and notes: what the agent says when a step cannot run. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read documents or the tracker on your behalf. "
    "Send it through the Nasiko platform."
)
GATEWAY_UNREACHABLE = "I couldn't reach the tool gateway, so I can't read the spec or the tracker. I won't guess."
DOC_NOT_FOUND = "I could not find that document. Check the title or link, or paste the spec after \"Spec:\"."
NO_DOC_TOOL = (
    "No document tool is available to this agent, so I can't open the spec by title or link: check that the "
    "document connector is enabled for it. You can paste the spec instead, after \"Spec:\"."
)
NO_DOC_TOOL_NOTE = "No document tool is available, so I worked from the pasted spec only."
NO_SPEC = (
    "Tell me which spec to break down: a document title or link, or paste the text after \"Spec:\"."
)
NO_TRACKER_NOTE = "No tracker tool is available: nothing can be created. Copy the issues below into your tracker."
NO_CREATE_TOOL_NOTE = "No issue-creating tool is available: nothing can be created. Copy the issues below instead."
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this may be incomplete."
UNREADABLE_ANSWER = "I read the spec but could not produce a list I can verify against it. Please ask again."
NOTHING_IN_SPEC = "I found no requirement in the spec that I could quote word for word, so I proposed no issues."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, then ask "
    "again."
)
WRITE_BLOCKED = "Creating is blocked for this agent, so nothing was created. Copy the issue text below instead."
DUP_CHECK_FAILED_NOTE = "The duplicate check could not search the tracker, so no issue is marked as existing."
NOTHING_SELECTED = "Nothing selected, so nothing was created."
CANCELLED = "Cancelled. Nothing more will be created."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
EDITS_EXHAUSTED = "I've applied the maximum of {rounds} edit rounds. Pick the issues to create, or Cancel."
UNKNOWN_TEAM = "That isn't one of the teams I listed. Ask again and name the team."
CARD_EDIT_HELP = "Card edits are \"title: <new title>\" or \"description: <new text>\"."
