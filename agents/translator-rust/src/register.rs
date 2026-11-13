//! Pure: the register (formal, neutral, casual) read from the instruction, the register row given to the
//! model for the target language, and the note that says which form was used.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::re;

pub const FORMAL: &str = "formal";
pub const NEUTRAL: &str = "neutral";
pub const CASUAL: &str = "casual";

static CASUAL_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\b(?:less formal|more casual|casual|informal|friendly|relaxed|colloquial|tu|tú|du)\b")
});
static FORMAL_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\b(?:more formal|formal|polite|business|professional|vous|sie|usted)\b")
});
static NEUTRAL_WORDS: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bneutral\b"));

/// base code: (formal second person, casual second person)
const T_V: &[(&str, &str, &str)] = &[
    ("fr", "vous", "tu"),
    ("de", "Sie", "du"),
    ("es", "usted", "tú"),
    ("it", "Lei", "tu"),
    ("pl", "Pan/Pani", "ty"),
    ("tr", "siz", "sen"),
    ("ru", "вы", "ты"),
    ("uk", "ви", "ти"),
    ("pt", "o senhor / a senhora", "você/tu"),
    ("nl", "u", "je/jij"),
    ("cs", "vy", "ty"),
    ("ro", "dumneavoastră", "tu"),
    ("el", "εσείς", "εσύ"),
];
/// base code: (formal, neutral, casual) level
const CJK_ROWS: &[(&str, [&str; 3])] = &[
    (
        "ja",
        [
            "keigo: desu/masu with honorific verbs where natural (です・ます、敬語)",
            "desu/masu (です・ます)",
            "plain form (だ・である), no keigo",
        ],
    ),
    (
        "ko",
        [
            "합쇼체 formal endings",
            "해요체 polite endings",
            "해체 plain endings",
        ],
    ),
    (
        "zh",
        [
            "use 您 for 'you'",
            "polite everyday tone, 你 is fine",
            "relaxed everyday tone with 你",
        ],
    ),
];

fn index(register: &str) -> usize {
    match register {
        FORMAL => 0,
        CASUAL => 2,
        _ => 1,
    }
}

fn pronouns(base: &str) -> Option<(&'static str, &'static str)> {
    T_V.iter()
        .find(|(b, ..)| *b == base)
        .map(|(_, f, c)| (*f, *c))
}

fn cjk_level(base: &str, register: &str) -> Option<&'static str> {
    CJK_ROWS
        .iter()
        .find(|(b, _)| *b == base)
        .map(|(_, row)| row[index(register)])
}

/// The register named in an instruction, or None. "less formal" is casual.
pub fn parse(text: &str) -> Option<&'static str> {
    if CASUAL_WORDS.is_match(text) {
        return Some(CASUAL);
    }
    if FORMAL_WORDS.is_match(text) {
        return Some(FORMAL);
    }
    NEUTRAL_WORDS.is_match(text).then_some(NEUTRAL)
}

/// The register row for the prompt.
pub fn rule(base: &str, register: &str) -> String {
    let mut general = match register {
        FORMAL => "Formal: courteous business register; formal second person.".to_string(),
        CASUAL => "Casual: informal second person, contractions, a relaxed tone.".to_string(),
        _ => concat!(
            "Neutral: polite everyday register; the polite second person for strangers or business ",
            "text, the familiar one only for clearly personal text."
        )
        .to_string(),
    };
    if let Some((formal, casual)) = pronouns(base) {
        let pronoun = if register == CASUAL { casual } else { formal };
        general.push_str(&format!(" In this language use '{pronoun}' for 'you'."));
    }
    if let Some(level) = cjk_level(base, register) {
        general.push_str(&format!(" Level: {level}."));
    }
    general
        + " Stay consistent for the whole text and keep the source's tone (a casual source is not \
           formalised under neutral)."
}

/// Which form was used, for the notes; empty when the language has no such choice.
pub fn note(base: &str, register: &str) -> String {
    if let Some((formal, casual)) = pronouns(base) {
        let used = if register == CASUAL { casual } else { formal };
        return format!("Used '{used}' for 'you' ({register}).");
    }
    cjk_level(base, register).map_or(String::new(), |level| format!("Politeness level: {level}."))
}
