"""Fixed replies. There is no model: every reply that is not a split or a merge is one of these."""

from __future__ import annotations

from smalltalk import INTRO

OFF_TOPIC_REPLY = "That's outside what I do: I only split text and merge results, so I can't help with it. " + INTRO
NEED_TEXT = "Paste the text to split as your answer, or cancel."
NEED_ENVELOPES = ("I need at least two JSON results to merge. Paste the other results (fenced json blocks, a "
                  "JSON array, or objects separated by ---) as your answer, or cancel.")
DIFFERENT_AGENTS = ("These look like results from different agents; I merge parts of one run. Paste results "
                    "that share the same fields.")
OVER_CAP = ("This piece would bring the held text to {total:,} characters, over the {limit:,} I can hold, so it "
            "was not added. I'm holding {kept:,} characters from earlier messages.")
CANCELLED = "Cancelled. Nothing was split or merged."
NO_SPLIT_YET = "There's no split in this conversation yet. Paste a text and say how to split it."
NO_MERGE_YET = "There's no merge in this conversation yet. Paste two or more JSON results with 'merge these'."
NO_SUCH_PART = "There's no part {n}: the last split has {total} part(s)."
APPENDED = ("Got it: {added:,} characters added, {total:,} held so far. Send the next piece, or say "
            "\"that's all\" to split it.")
NEED_TEXT_WITH_PART = "Paste this piece's text after the first line (\"part 1 of 3, more coming\" then the text)."
NOTHING_HELD = "I'm not holding any text yet. Paste the text to split."
TOKEN_NOTE = "Token counts are estimates (characters / 4); real counts vary, especially for non-English text."
