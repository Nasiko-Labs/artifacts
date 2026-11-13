"""Fixed replies. There is no model: every reply that is not a split or a merge is one of these."""

from __future__ import annotations

from smalltalk import INTRO

OFF_TOPIC_REPLY = "That's outside what I do: I only split text and merge results, so I can't help with it. " + INTRO
NEED_TEXT = ("There's no text to split in that message. Paste the text in your next message and I'll split it "
             "with the options you gave.")
NEED_ENVELOPES = ("I need at least two JSON results to merge. Paste the other results (fenced json blocks, a "
                  "JSON array, or objects separated by ---) in your next message and I'll merge them with this one.")
DIFFERENT_AGENTS = ("These look like results from different agents; I merge parts of one run. Paste results "
                    "that share the same fields.")
OVER_CAP_CUT = ("This text is {total:,} characters; I split the first {kept:,} and the last {left:,} were left out. "
                "Send the rest in another message to split it too.")
PIECE_NOTE = ("I keep nothing between messages, so I split each piece as it arrives (parts are numbered within "
              "this piece). To split the whole text as one, send it in one message or as a file.")
NO_SPLIT_YET = "There's no split in this conversation yet. Paste a text and say how to split it."
NO_MERGE_YET = "There's no merge in this conversation yet. Paste two or more JSON results with 'merge these'."
SPLIT_NOT_IN_HISTORY = ("I can't see the text you asked me to split in the recent conversation any more (long "
                        "pastes get trimmed), so please paste it again with how to split it.")
MERGE_NOT_IN_HISTORY = ("I can't see the results from the earlier merge in the recent conversation any more (long "
                        "pastes get trimmed), so please paste all the results again with 'merge these'.")
NO_SUCH_PART = "There's no part {n}: the last split has {total} part(s)."
NEED_TEXT_WITH_PART = "Paste this piece's text after the first line (\"part 1 of 3, more coming\" then the text)."
NOTHING_HELD = ("I keep nothing between messages: each piece was split when it arrived. Paste text to split "
                "it.")
TOKEN_NOTE = "Token counts are estimates (characters / 4); real counts vary, especially for non-English text."
