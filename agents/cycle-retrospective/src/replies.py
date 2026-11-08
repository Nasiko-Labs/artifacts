"""Fixed replies: what the agent says when a step cannot run. No LLM call produces these."""

NO_TRACE_CONTEXT = (
    "This request arrived without a trace context, so I can't read the tracker or chat on your behalf. Send it "
    "through the Nasiko platform."
)
GATEWAY_UNREACHABLE = (
    "I couldn't reach the tool gateway, so I can't read the cycle. I won't guess the numbers from memory."
)
NO_TOOLS = (
    "No tracker or chat tool is available to this agent: check that those connectors are enabled for it."
)
NO_TRACKER_TOOL = (
    "No tracker tool is available to this agent, so I have no cycle to compute a retro from: check that the "
    "tracker connector is enabled for it. I won't guess the numbers from memory."
)
NO_CYCLE = "I couldn't find that cycle in the tracker. Name it by number (\"cycle 24\") or say \"last cycle\"."
NO_FINISHED_CYCLE = "I found no finished cycle for {team} yet, so there is nothing to look back on."
NO_CURRENT_CYCLE = "{team} has no cycle running today."
NO_TEAM = "I couldn't find a team in the tracker that I can back with its own data."
UNKNOWN_TEAM = "That isn't one of the teams I listed. Ask again and name the team."
ASK_TEAM = "Which team?"
UNREADABLE_ANSWER = "I looked, but could not produce a result I can verify. Please ask again."
STEP_LIMIT_NOTE = "I hit my step or time limit before finishing, so this may be incomplete."
READ_NEEDS_APPROVAL = (
    "A tool I need for reading is set to require approval. Ask the admin to allow reads for this agent, "
    "then ask again."
)
NO_ISSUES = "The tracker returned no issues for this cycle that I can verify, so there are no numbers to show."
CHAT_NOT_SEARCHED = "Chat was not searched; reasons come from issue comments only."
NO_RETRO_YET = "I haven't built a retro in this conversation yet. Ask for one first, for example \"Retro for the last cycle\"."
NO_CHANNEL = "Tell me which channel to post to, for example \"post it to #eng-retro\"."
NOT_POSTED = "Skipped: nothing was posted. The retro above is yours to copy."
EDIT_HINT = ("I only understand \"drop prompts\" or \"drop spilled\" as edits here (free text is not sent to a "
             "model). Pick Post or Skip, or type one of those.")
NO_WRITE_TOOL = "No granted chat tool can post a message, so I can't post the retro. Copy it from above instead."
