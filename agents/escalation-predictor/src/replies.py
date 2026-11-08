"""Fixed replies: what the agent says when a step cannot run. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read the support desk or the tracker on your "
    "behalf. Send it through the Nasiko platform."
)
GATEWAY_UNREACHABLE = (
    "I couldn't reach the tool gateway, so I can't read your tickets. I won't guess from memory."
)
NO_DESK_PASTE_HINT = (
    "No support-desk tool is available to this agent, so there are no tickets to score. Either enable the "
    "support-desk connector for this agent, or paste tickets in this chat, one block per ticket:\n\n"
    "```\nid: 4812\npriority: urgent\ntier: enterprise\ncustomer: Acme\nsubject: Exports fail again\n"
    "created: 2026-10-27\ncustomer 2026-11-03 09:10: Exports fail again. This is unacceptable.\n"
    "agent 2026-11-03 11:00: Thanks, we are looking into it.\n```"
)
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this may be incomplete."
UNREADABLE_ANSWER = "I looked, but could not produce a result I can verify. Please ask again."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, "
    "then ask again."
)
NO_TICKETS = "I found no open support tickets in the last {days} days that I can back with the desk's own data."
NO_LIST_YET = "I haven't scored any tickets in this conversation yet. Ask \"Which tickets are about to escalate?\" first."
UNKNOWN_TICKETS = "I don't have {ids} in the tickets I scored, so I can't draft a status reply for it."
TRACKER_NOT_CHECKED = "Known bugs were not checked"
CAP_NOTE = "Read the {n} most recently updated open tickets (the cap per run); older ones were not scored."
COMMENTS_NOTE = ("Comments could not be read for {n} ticket(s): their unanswered and escalation-language factors "
                 "are unknown and not scored.")
DROPPED_NOTE = "{n} item(s) could not be verified against the source data and were left out."
NO_TOOLS = "No support-desk or tracker tool is available to this agent."
WHICH_TICKET = "Tell me which ticket to draft a status reply for, for example \"draft a status reply for #4812\"."
