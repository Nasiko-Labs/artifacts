"""Pure: the register (formal, neutral, casual) read from the instruction, the register row given to the
model for the target language, and the note that says which form was used."""

from __future__ import annotations

import re

FORMAL, NEUTRAL, CASUAL = "formal", "neutral", "casual"
REGISTERS = (FORMAL, NEUTRAL, CASUAL)

_CASUAL = re.compile(r"\b(?:less formal|more casual|casually|informally|casual|informal|friendly|relaxed|"
                     r"colloquial|tu|tú|du)\b",
                     re.IGNORECASE)
_FORMAL = re.compile(r"\b(?:more formal|formally|politely|formal|polite|business|professional|vous|sie|usted)\b",
                     re.IGNORECASE)
_NEUTRAL = re.compile(r"\bneutral\b", re.IGNORECASE)

# base code: (formal second person, casual second person)
_T_V = {"fr": ("vous", "tu"), "de": ("Sie", "du"), "es": ("usted", "tú"), "it": ("Lei", "tu"),
        "pl": ("Pan/Pani", "ty"), "tr": ("siz", "sen"), "ru": ("вы", "ты"), "uk": ("ви", "ти"),
        "pt": ("o senhor / a senhora", "você/tu"), "nl": ("u", "je/jij"), "cs": ("vy", "ty"),
        "ro": ("dumneavoastră", "tu"), "el": ("εσείς", "εσύ")}
_CJK_ROWS = {
    "ja": ("keigo: desu/masu with honorific verbs where natural (です・ます、敬語)", "desu/masu (です・ます)",
           "plain form (だ・である), no keigo"),
    "ko": ("합쇼체 formal endings", "해요체 polite endings", "해체 plain endings"),
    "zh": ("use 您 for 'you'", "polite everyday tone, 你 is fine", "relaxed everyday tone with 你"),
}


def parse(text: str) -> str | None:
    """The register named in an instruction, or None. "less formal" is casual."""
    if _CASUAL.search(text or ""):
        return CASUAL
    if _FORMAL.search(text or ""):
        return FORMAL
    if _NEUTRAL.search(text or ""):
        return NEUTRAL
    return None


def rule(base: str, register: str) -> str:
    """The register row for the prompt."""
    general = {
        FORMAL: "Formal: courteous business register; formal second person.",
        NEUTRAL: ("Neutral: polite everyday register; the polite second person for strangers or business "
                  "text, the familiar one only for clearly personal text."),
        CASUAL: "Casual: informal second person, contractions, a relaxed tone.",
    }[register]
    if base in _T_V:
        formal, casual = _T_V[base]
        pronoun = casual if register == CASUAL else formal
        general += f" In this language use '{pronoun}' for 'you'."
    if base in _CJK_ROWS:
        general += " Level: " + _CJK_ROWS[base][REGISTERS.index(register)] + "."
    return (general + " Stay consistent for the whole text and keep the source's tone (a casual source is not "
            "formalised under neutral).")


def note(base: str, register: str) -> str:
    """Which form was used, for the notes; empty when the language has no such choice."""
    if base in _T_V:
        formal, casual = _T_V[base]
        used = casual if register == CASUAL else formal
        return f"Used '{used}' for 'you' ({register})."
    if base in _CJK_ROWS:
        return f"Politeness level: {_CJK_ROWS[base][REGISTERS.index(register)]}."
    return ""
