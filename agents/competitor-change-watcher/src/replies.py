"""Fixed replies: written once here, sent without a model call."""

NO_TRACE_CONTEXT = ("I can't run this request: it arrived without trace context, so the platform would refuse my "
                    "model calls. Please send it again through Nasiko chat.")
GATEWAY_UNREACHABLE = "The tool gateway couldn't be reached, so nothing was fetched."
STEP_LIMIT_NOTE = ("I ran out of time or model calls before classifying every entry; unclassified entries are "
                   "listed as `other` with their first sentence.")
FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
FOLLOWUP_UNVERIFIED = "I couldn't answer that from the last report without new numbers, so here is the report again."
SNAPSHOT_NOTE = ("Baselines are the text you pasted earlier in this conversation; a new chat starts without one.")
EXAMPLE = """Competitor: Acme
Source: changelog
2026-10-28 - Native SAML SSO for Team plans
2026-10-14 - Threaded replies in comments

Competitor: Acme
Source: pricing
Before:
Team: $20/seat/mo, 10 projects
After:
Team: $24/seat/mo, 25 projects"""
FORMAT_HELP = (
    "I work on competitor changelog and pricing text you paste into the chat (up to 100,000 characters); I don't "
    "open web pages myself. Paste one block per competitor and source:\n"
    "- `Competitor: Name` and `Source: changelog` or `Source: pricing` header lines, then the text.\n"
    "- Optional `Before:` and `After:` lines to compare two versions; otherwise I compare with what you pasted "
    "earlier in this conversation, or list entries from the last 30 days.\n"
    "Put your question on top, for example \"what changed and what does it hit on our roadmap?\".\n\nExample:\n```\n"
    + EXAMPLE + "\n```"
)
URL_HINT = ("No page-reading tool is connected to this agent, so I can't open that link: paste the competitor's "
            "changelog or pricing text instead.")
