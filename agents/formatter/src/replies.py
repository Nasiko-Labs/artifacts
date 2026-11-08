"""Fixed replies. There is no model: every reply that is not a rendering is one of these."""

from __future__ import annotations

from smalltalk import INTRO

OFF_TOPIC_REPLY = "That's outside what I do, so I can't help with it here. " + INTRO
NO_JSON = "There's nothing to format yet: paste the JSON (or the Markdown) in the same message. " + INTRO
TOO_BIG = ("That's {chars:,} characters; I format up to {limit:,} characters of JSON per message. Paste a "
           "smaller part, or one envelope at a time.")
JSON_ERROR = ("That JSON didn't parse ({error}). Fix it and paste it again; I format exactly what you "
              "give me, so I don't guess at broken JSON.")
CANCELLED = "Cancelled. Paste JSON whenever you want it formatted."
NOT_FAITHFUL = "Could not render as {target} without changing content, so here it is as Markdown."
NOT_FAITHFUL_ANY = "Could not render this without changing content, so here is the JSON as given."
