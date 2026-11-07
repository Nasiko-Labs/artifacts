"""Fixed replies: what the agent says when a step cannot run. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read your support desk, chat, CRM or tracker on "
    "your behalf. Send it through the Nasiko platform."
)
GATEWAY_UNREACHABLE = "I couldn't reach the tool gateway, so I can't read any feedback source. I won't guess from memory."
NO_TOOLS = "No tool is available to this agent."
NO_SOURCES_PASTE_HINT = (
    "I couldn't read any feedback source (support desk or chat). Connect one of them for this agent, or paste "
    "the feedback here: one item per block, separated by blank lines, with optional `customer:`, `date:` and "
    "`tier:` lines."
)
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this result may be incomplete."
TOO_FEW = "Three items or more are needed to find a theme."
NO_THEME = "No theme reached three items or two customers."
NO_RESULT_YET = (
    "I haven't pulled any feedback in this conversation yet. Ask for \"feedback themes from the last 30 days\"."
)
UNKNOWN_THEME = "There is no theme {n} in the last result. Pick a number from the list."
CANNOT_ANSWER = "The stored result doesn't say, and I won't guess. Ask me to pull the feedback again."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, then ask "
    "again."
)
CONTINUE_HINT = (
    "I read at most {cap} items per pull to stay within my time limit. Narrow the window (for example \"last 2 "
    "weeks\") or name a channel to see the rest."
)
NO_TRACKER = "Existing issues were not checked: no tracker tool is available to this agent."
