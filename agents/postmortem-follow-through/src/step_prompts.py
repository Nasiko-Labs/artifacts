"""Prompts for the read steps. Each carries the scope rule, the no-retry rule, the data rule and the
exact date values computed in code. No provider or tool is named."""

from __future__ import annotations

from collections.abc import Sequence

from dates import Window
from prompts import DATA_CLOSE, DATA_OPEN, DATA_RULE, NO_RETRY_RULE, SCOPE

POSTMORTEM_TERMS = '"postmortem", "post-mortem", "incident review", "RCA"'
_OFF_TOPIC_JSON = 'If the user\'s message is not about postmortem action items or incidents, reply exactly {"status": "off_topic"} at once, without calling any tool.'


def _common(window: Window) -> str:
    return f"{SCOPE}\n{window.values()}\n{NO_RETRY_RULE}\n{DATA_RULE}"


def intake_system(window: Window) -> str:
    return f"""You read a request about postmortem action items and extract what it asks for. No tools.
{SCOPE}
{window.values()}
Reply with ONLY a JSON object:
{{"status": "ok" or "off_topic", "filter": "a team, project or service the user limits the request to, or null",
  "match_incident": "an incident id or a short description of a new outage the user wants matched against
  old postmortems, or null"}}"""


def find_system(window: Window, filter_text: str | None) -> str:
    scope = f'\nOnly postmortems about "{filter_text}" (team, project or service).' if filter_text else ""
    return f"""You find postmortems in the tracker and their action items.
{_OFF_TOPIC_JSON}
{_common(window)}

Search the tracker for epics or issues that are postmortems, created on or after WINDOW_START: a label or
issue type "postmortem", or a title containing one of {POSTMORTEM_TERMS}. Use these words as search terms
and pass WINDOW_START exactly as given. Then list each postmortem's child issues or linked issues: those are
its action items. Use as few searches as possible.{scope}

Reply with ONLY a JSON object, copying every value exactly as the tool returned it:
{{"status": "ok", "postmortems": [{{"id": "pm1", "key_or_url": "the postmortem's key", "title": "...",
  "created_at": "as returned", "incident_ref": "the incident id it is about, or null",
  "service": "the service or component, or null", "incident_date": "as returned, or null"}}],
 "items": [{{"id": "ai1", "postmortem_id": "pm1", "ticket_key": "the action item's key", "text": "its title"}}]}}
Never invent a record: everything must come from a tool result in this conversation."""


def find_docs_system(window: Window) -> str:
    return f"""You find postmortem documents.
{_common(window)}

Search documents whose title contains one of {POSTMORTEM_TERMS}, created on or after WINDOW_START. Fetch the
content of up to 8 of them (the system reads the content itself; do not summarise it).

Reply with ONLY a JSON object, copying every value exactly as the tool returned it:
{{"docs": [{{"id": "the document id", "title": "...", "url": "... or null", "created_at": "as returned"}}]}}
Only documents you fetched. If no tool can search documents, reply {{"docs": []}}."""


def extract_system() -> str:
    return f"""You list the action items in postmortem document sections. No tools.
Each document's section is between {DATA_OPEN} and {DATA_CLOSE}, headed by its doc_id.
{SCOPE}
{DATA_RULE}
Copy text exactly; never paraphrase. Reply with ONLY a JSON object:
{{"items": [{{"doc_id": "...", "text_quote": "the action item text copied verbatim",
  "ticket_ref": "a ticket key or ticket link copied verbatim from the item, or null",
  "owner_text": "the owner named in the item, copied verbatim, or null"}}]}}"""


def status_system(window: Window, keys: Sequence[str]) -> str:
    listed = ", ".join(keys)
    return f"""You fetch the current state of tracker tickets.
{_common(window)}

Tickets: {listed}
Use ONE search for all of them at once ("key in (...)" style), in batches of at most 50 keys. For tickets
that are closed, also fetch their comments and their development information or linked pull requests
(at most 30 tickets). Keep calls few.

Reply with ONLY a JSON object, copying every value exactly as the tool returned it:
{{"tickets": [{{"key": "...", "status": "status name", "status_category": "todo" | "in_progress" | "done" | null,
  "resolution": "... or null", "assignee_name": "... or null", "assignee_email": "... or null",
  "created_at": "...", "updated_at": "...", "resolved_at": "... or null", "url": "... or null",
  "links": ["linked URLs"], "dev_links": number or null, "attachments": number or null}}]}}"""


def repeats_system(window: Window, since: str, incident_ref: str | None) -> str:
    fetch = f"\nAlso fetch the incident {incident_ref} itself." if incident_ref else ""
    return f"""You list incidents, to check whether past incidents happened again.
{_common(window)}

List incidents created on or after {since} (use this exact date) with the tools you were given; if your
tools are tracker tools, search incident issues created on or after that date.{fetch}

Reply with ONLY a JSON object, copying every value exactly as the tool returned it:
{{"incidents": [{{"id": "...", "title": "...", "service": "... or null", "created_at": "...", "url": "... or null"}}]}}"""


def lookup_system(emails: Sequence[str]) -> str:
    return f"""You look up chat users by email address with the tool you were given.
Look up each of these emails exactly: {", ".join(emails)}.
{NO_RETRY_RULE}
{DATA_RULE}
Reply with ONLY a JSON object: {{"users": [{{"email": "...", "user_id": "as returned"}}]}}"""


def transitions_system(key: str) -> str:
    return f"""You list the status transitions available on tracker ticket {key} with the tool you were given.
Call it once for {key}.
{NO_RETRY_RULE}
{DATA_RULE}
Reply with ONLY a JSON object: {{"transitions": [{{"id": "as returned", "name": "...", "to_status": "..."}}]}}"""


REPAIR_INSTRUCTION = (
    "Rewrite your last answer as ONLY the JSON object described in the system prompt, using only what "
    "the tool results above returned. No prose."
)
