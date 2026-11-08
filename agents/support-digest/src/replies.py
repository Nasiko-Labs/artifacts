"""Fixed replies and notes: what the agent says when a step cannot run. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read the support desk on your behalf. Send it "
    "through the Nasiko platform."
)
GATEWAY_UNREACHABLE = "I couldn't reach the tool gateway, so I can't read your tickets. I won't guess from memory."
NO_DESK_PASTE_HINT = (
    "No support-desk tool is available to this agent, so there are no tickets to digest. Either enable the "
    "support-desk connector for this agent, or paste tickets in this chat, one block per ticket:\n\n"
    "```\nid: 4812\ncreated: 2026-11-03 09:00\nfirst reply: none\nsolved: none\npriority: urgent\nstatus: open\n"
    "customer: Acme\nsubject: Cannot log in after update\n```"
)
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this may be incomplete."
UNREADABLE_ANSWER = "I looked, but could not produce a result I can verify. Please ask again."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, then ask "
    "again."
)
NO_TICKETS = "I found no support tickets created from {start} to {end} that I can back with the desk's own data."
NO_DIGEST_YET = "I haven't built a digest in this conversation yet. Ask \"Support digest for last week\" first."
NO_TOOLS = "No support-desk tool is available to this agent."

SLA_DEFAULTS_NOTE = "SLA targets are defaults: no desk SLA policy was readable"
SLA_DESK_NOTE = "SLA targets from the desk's policies"
SLA_PARTLY_NOTE = "defaults used for priority {names}"
CALENDAR_NOTE = "SLA times are calendar time, not business hours."
SAMPLE_NOTE = ("The desk reported {total} tickets; I read the newest {n} (the cap per run), so themes, SLA and "
               "customers are based on a sample of {n}.")
CAP_NOTE = "More than {n} tickets were returned; the newest {n} were used (the cap per run)."
PREVIOUS_UNREAD = "The previous period's count could not be read, so there is no trend."
PREVIOUS_SAMPLED = "The previous period's count is a sample (the search returned more than it counted)."
PASTED_NO_PREVIOUS = "No pasted ticket falls in the previous period, so there is no trend."
UNKNOWN_NOTE = ("{n} ticket(s) have a first reply or resolution time I could not read: counted as unknown, never "
                "as breached.")
THEMES_PARTIAL = "Themes cover {covered} of {total} tickets (a labelling step failed or the cap was reached)."
CLUSTER_FALLBACK = "Similar theme labels could not be grouped, so each label is its own theme."
DROPPED_NOTE = "{n} item(s) could not be verified against the desk's data and were left out."
FILTER_NOTE = "Filtered to {what} (both periods)."
AMBIGUOUS_NOTE = "I did not recognise the tier or priority you named, so I did not filter by it."
