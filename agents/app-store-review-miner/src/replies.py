"""Fixed replies: written once here, sent without a model call."""

NO_TRACE_CONTEXT = ("I can't run this request: it arrived without trace context, so the platform would refuse my "
                    "model calls. Please send it again through Nasiko chat.")
GATEWAY_UNREACHABLE = "The tool gateway couldn't be reached, so no reviews were fetched."
STEP_LIMIT_NOTE = ("I ran out of time or model calls before reading every review; the results below cover only "
                   "the reviews listed as analysed.")
FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
FOLLOWUP_UNVERIFIED = "I couldn't answer that from the analysis without new numbers, so here is the analysis again."
EXAMPLE = """App: Acme Notes
Rating: 2
Version: 4.2.1
Date: 2026-11-02
Store: iOS
Keeps asking me to sign in again after the update.

---
Rating: 5
Version: 4.2.0
Great app, syncs fast."""
FORMAT_HELP = (
    "I need the reviews themselves: paste them into the chat (up to 100,000 characters) in one of these shapes.\n"
    "1. Blocks separated by a blank line or `---`, with optional `Rating:`, `Version:`, `Date:`, `Store:` "
    "(iOS/Android), `App:` and `Title:` lines, then the review text.\n"
    "2. CSV or TSV with a header row (rating, version, date, store, app, title, body).\n"
    "3. A JSON list of objects with the same keys.\n"
    "Put `App: Name` before each app's reviews; the first app is yours unless you add `Ours: Name`. "
    "Put your question on top.\n\nExample:\n```\n" + EXAMPLE + "\n```"
)
