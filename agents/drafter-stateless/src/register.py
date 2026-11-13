"""Pure: the channel register table (subject, sign-off, bullets, length caps) and the words that name a
channel. The table overrides the tone profile where they conflict."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHANNELS = ("email", "chat", "text", "linkedin")
LENGTHS = ("short", "medium", "long")


@dataclass(frozen=True)
class Register:
    channel: str
    label: str
    subject: bool          # a subject line is required (email) or forbidden (everything else)
    sign_off: bool         # a sign-off block is allowed
    bullets: bool          # bullet lists are allowed
    unit: str              # "words" | "chars"
    caps: tuple[int, int, int]  # short, medium, long
    max_paragraphs: int | None
    guidance: str

    def cap(self, length: str) -> int:
        return self.caps[LENGTHS.index(length)] if length in LENGTHS else self.caps[1]

    def size(self, body: str) -> int:
        return len(body) if self.unit == "chars" else len(body.split())


REGISTERS = {
    "email": Register("email", "Email", True, True, True, "words", (90, 200, 350), None,
                      "Subject line required (at most 80 characters, sentence case). Greeting and sign-off "
                      "per the tone profile. Short paragraphs."),
    "chat": Register("chat", "Chat message", False, False, True, "words", (40, 80, 150), 3,
                     "No subject. A greeting is optional; no sign-off. At most 3 short paragraphs or a few "
                     "bullets."),
    "text": Register("text", "Text message", False, False, False, "chars", (320, 320, 320), 1,
                     "No subject, no greeting line, no sign-off. One paragraph, no bullets."),
    "linkedin": Register("linkedin", "LinkedIn message", False, False, False, "words", (120, 120, 120), None,
                         "No subject. Greeting per the tone profile; no sign-off block; no bullets."),
}
MAX_SUBJECT_CHARS = 80

# Words a user types to name where a message goes. The last mention wins ("turn this email into a
# chat message" means chat).
_CHANNEL_WORDS = (
    ("email", r"e-?mails?|mail"),
    ("chat", r"slack|ms teams|on teams|in teams|teams (?:message|chat)|dm|direct message|chat(?: message)?"),
    ("text", r"text message|sms|whats ?app|imessage|a text|text (?=him|her|them|me)"),
    ("linkedin", r"linked ?in"),
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
