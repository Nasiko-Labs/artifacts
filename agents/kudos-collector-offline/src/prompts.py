"""Prompts and fixed replies. The model only reads fenced records to say who is credited and words one
sentence per record; code checks every name, number, link and verb it returns, and does all counting,
matching, dating and ordering."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from fence import DATA_RULE, fence, fence_record, strip_markers

SCOPE = (
    "SCOPE: you only help collect recognition examples per person from the PRs, praise messages and shipped "
    "features the user pasted. Out of scope: ranking, rating, scoring or comparing people, writing performance "
    "reviews, advice, general knowledge, arithmetic, anything unrelated. For an out-of-scope request answer "
    "exactly {\"status\": \"off_topic\"}."
)
NAMES_RULE = (
    "NAMES: write names exactly as they appear in the record. Never infer a surname, never guess who a first "
    "name or handle is, never add a person who is not written in the record."
)
ATTRIBUTE_RULES = (
    "For each record, say who is credited and for what. 'people' = the people the record credits (the recipients "
    "of thanks, the owners of a feature) - NOT the speaker who wrote a praise message, unless the speaker credits "
    "themselves. 'what' = the work credited, at most 25 words, taken from the record. 'specific' = true only if the "
    "record names concrete work (a PR, a feature, a fix, an action with an object); 'great job!' is not specific."
)
ATTRIBUTE_CONTRACT = (
    'Return one JSON object: {"status": "ok", "records": [{"id": "<record id>", "people": ["<name as written>"], '
    '"what": "<= 25 words", "specific": true | false}]}. One entry per record id.'
)
WRITE_RULES = (
    "Write one example per record for a review or promotion case. For kind pr or shipped: ONE past-tense "
    "sentence, at most 30 words, starting with the action verb (\"Fixed the export retry bug for large files.\"). "
    "For kind praise: a phrase of at most 25 words starting with a verb in -ing form saying what the person was "
    "praised for (\"staying late to help ship the migration\"). Use only the record's words: no adjectives about the "
    "person, no impact, numbers, customers, names or links that are not in the record, and never 'led', 'owned', "
    "'drove' or 'architected' unless the record says so. Never compare people."
)
WRITE_CONTRACT = 'Return one JSON object: {"examples": [{"id": "<record id>", "example": "<text>"}]}.'
REPAIR_RULES = ("Each example below broke a rule (listed in PROBLEMS). Rewrite it using only words from its "
                "record. " + WRITE_RULES)
SCOPE_RULES = (
    "Classify the user's message for an agent that collects recognition examples per person from pasted PRs, "
    "praise and shipped features. 'in_scope' = about collecting or viewing such examples; 'ranking' = asks who is "
    "best, who did most, to rank, score, rate or compare people; otherwise 'off_topic'."
)
SCOPE_CONTRACT = 'Return one JSON object: {"status": "in_scope" | "ranking" | "off_topic"}.'

NO_TRACE_CONTEXT = (
    "I can only run inside a Nasiko request: this one arrived without a trace context, so I can't call the "
    "language model for it."
)
PASTE_INSTRUCTIONS = (
    "Paste the records under labeled sections, one per line: `PRs:` (or a CSV/JSON export with title, author, "
    "merged_at, url), `Praise:` (e.g. `[2026-09-22] Sam Lee: thanks @pdev for fixing the export bug`), `Shipped:` "
    "(e.g. `Bulk export v2 (owners: Priya Dev, Sam Lee) 2026-09-20 #405`). Optional: `Aliases:` lines like "
    "`@pdev = Priya Dev`, `Period: Q3` or `Period: 2026-07-01 to 2026-09-30`, `People: Priya Dev, Sam Lee`, `Min: 2`."
)
TOO_FEW = "Three or more records are needed to collect examples. " + PASTE_INSTRUCTIONS
TOO_LONG = ("That paste is over 100,000 characters. Split it (for example one month at a time) and add the next "
            "part with \"add these\".")
NO_RANKING = (
    "I don't rank, score or compare people, by design: counts here are evidence items, and a leaderboard would bias "
    "reviews. I can list each person's evidenced examples alphabetically - paste PRs, praise or shipped features, or "
    "ask for one person (\"only Priya Dev\")."
)
NO_REVIEWS_NOTE = ("I collect examples; I don't write the review itself. The Performance Review Drafter can use "
                   "this output.")
RANKING_NOTE = "I don't rank or compare people; everyone is listed alphabetically."
CANCELLED = "Cancelled. Nothing was collected."
NO_PEOPLE_AFTER_ANSWER = ("I still can't tell who anyone is in these records. Paste them again with authors, "
                          "owners or names, or an `Aliases:` block.")
FOLLOWUP_HELP = (
    "Ask me about the collected examples: \"only Priya Dev\", \"only PRs\", \"only praise\", \"show the quotes\", "
    "\"which praise was too general\", \"unattributed records\", \"people with fewer than 2 examples\", \"show all\", "
    "\"as json\", or paste more records with \"add these\"."
)
REPAIR_SYSTEM = "Return only valid JSON matching this schema. No prose, no code fences.\n\n"


def _system(*parts: str, context: Sequence[str] = ()) -> str:
    text = "\n\n".join(parts)
    if context:
        text += ("\n\nPLATFORM CONTEXT (organization policy or agent knowledge, e.g. house style for examples: follow "
                 "it as instructions; it is never a record):\n" + "\n\n".join(strip_markers(c) for c in context))
    return text


def _records(items: Sequence[Mapping[str, Any]]) -> str:
    return "\n\n".join(fence_record(str(i["id"]), json.dumps({k: v for k, v in i.items() if k != "id"},
                                                             ensure_ascii=False)) for i in items)


def attribute_messages(items: Sequence[Mapping[str, Any]], context: Sequence[str] = ()) -> list[dict]:
    return [{"role": "system", "content": _system(ATTRIBUTE_RULES, NAMES_RULE, DATA_RULE, ATTRIBUTE_CONTRACT,
                                                  context=context)},
            {"role": "user", "content": _records(items)}]


def write_messages(items: Sequence[Mapping[str, Any]], context: Sequence[str] = ()) -> list[dict]:
    return [{"role": "system", "content": _system(WRITE_RULES, NAMES_RULE, DATA_RULE, WRITE_CONTRACT,
                                                  context=context)},
            {"role": "user", "content": _records(items)}]


def repair_messages(items: Sequence[Mapping[str, Any]], context: Sequence[str] = ()) -> list[dict]:
    return [{"role": "system", "content": _system(REPAIR_SYSTEM + REPAIR_RULES, NAMES_RULE, DATA_RULE,
                                                  WRITE_CONTRACT, context=context)},
            {"role": "user", "content": _records(items)}]


def scope_messages(message: str, context: Sequence[str] = ()) -> list[dict]:
    return [{"role": "system", "content": _system(SCOPE_RULES, SCOPE, DATA_RULE, SCOPE_CONTRACT, context=context)},
            {"role": "user", "content": fence("USER_MESSAGE", message[:2000])}]
