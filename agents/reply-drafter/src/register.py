"""Pure: the channel register table (subject, greeting, sign-off, bullets, length caps) and the words that
name a channel (copied from the drafter agent and trimmed to email, chat and text; owned here). The table
overrides the measured style where they conflict."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHANNELS = ("email", "chat", "text")
MAX_SUBJECT_CHARS = 80


@dataclass(frozen=True)
class Register:
    channel: str
    label: str
    subject: bool             # a subject line is expected (email) or left out (everything else)
    greeting: bool            # the measured greeting applies
    sign_off: bool            # a sign-off block is allowed (and expected when the examples have one)
    bullets: bool             # bullet lists may be used (if the examples use them)
    max_words: int
    max_chars: int | None
    max_paragraphs: int | None
    guidance: str

    def over_cap(self, body: str) -> str | None:
        """The cap violation, worded for the repair call, or None."""
        words = len(body.split())
        if words > self.max_words:
            return f"the reply is {words} words; the cap for a {self.label.lower()} is {self.max_words} words"
        if self.max_chars and len(body) > self.max_chars:
            return (f"the reply is {len(body)} characters; the cap for a {self.label.lower()} is "
                    f"{self.max_chars} characters")
        return None


REGISTERS = {
    "email": Register("email", "Email", True, True, True, True, 200, None, None,
                      "Greeting and sign-off as measured. Short paragraphs."),
    "chat": Register("chat", "Chat message", False, True, False, True, 80, None, 3,
                     "No subject and no sign-off. At most 3 short paragraphs."),
    "text": Register("text", "Text message", False, False, False, False, 50, 320, 1,
                     "No subject, no greeting line, no sign-off, no bullets. One paragraph."),
}

# Words a user types to name where the reply goes. The last mention wins.
_CHANNEL_WORDS = (
    ("email", r"e-?mails?|mail"),
    ("chat", r"slack|ms teams|teams|dm|direct message|chat(?: message)?"),
    ("text", r"text message|text|sms|whats ?app|imessage"),
)
_CHANNEL_RE = re.compile("|".join(f"(?P<{name}>\\b(?:{words})\\b)" for name, words in _CHANNEL_WORDS),
                         re.IGNORECASE)


def detect_channel(text: str) -> str | None:
    """The channel the user names, by its last mention, or None."""
    found = None
    for match in _CHANNEL_RE.finditer(text or ""):
        found = match.lastgroup
    return found


def register_for(channel: str) -> Register:
    return REGISTERS.get(channel, REGISTERS["email"])
