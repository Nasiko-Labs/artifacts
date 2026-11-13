//! Pure: a short message about the last translation (rebuilt from the history; nothing is stored).
//! "show the original" costs no call and "as json" one run; "more formal", "now in French", "keep 'X' as
//! is" re-translate the earlier source with the change applied. A short message is a follow-up only when
//! nothing is left once language names, register words, glossary entries and filler are removed, so a
//! short text such as "I speak German" is still text to translate.

use std::sync::LazyLock;

use regex::Regex;

use crate::languages;
use crate::register;
use crate::request::{self, Job};
use crate::util::{collapse_ws, re};

pub const FOLLOWUP_MAX_WORDS: usize = 12;

static ORIGINAL: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^(?:show|see|give|display)?\s*(?:me\s+)?(?:the\s+)?(?:original|source)(?: text| again)?$")
});
static JSON: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"^(?:(?:show|give)(?: me)?(?: it| that| the result)?\s+)?(?:as|in)?\s*json(?: only| please)?$",
    )
});
static FILLER: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:now|again|please|pls|in|into|to|make|it|this|that|the|same|a|bit|little|more|less|much|and|but|",
        r"translate|translation|version|tone|register|instead|also|then|do|can|you|could|same text|use|with|",
        r"one|thanks|ok|okay|traditional|simplified)\b"
    ))
});
static REGISTER_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:formal|polite|business|professional|casual|informal|friendly|relaxed|",
        r"colloquial|neutral|vous|sie|usted|tu|tú|du)\b"
    ))
});
static PUNCT: LazyLock<Regex> = LazyLock::new(|| re(r"[^\w\s]"));

#[derive(Debug, Clone, PartialEq)]
pub enum FollowUp {
    Original,
    Json,
    Retranslate(Box<Job>),
}

/// With nothing recalled from the history (`stored` None) a follow-up still parses, on an empty job: the
/// caller then says the earlier text is gone instead of treating "more formal" as text to translate.
pub fn parse(text: &str, stored: Option<&Job>) -> Option<FollowUp> {
    let message = collapse_ws(text);
    let words = message.split_whitespace().count();
    if message.is_empty() || words > FOLLOWUP_MAX_WORDS {
        return None;
    }
    let plain = collapse_ws(&PUNCT.replace_all(&message.to_lowercase(), " "));
    if ORIGINAL.is_match(&plain) {
        return Some(FollowUp::Original);
    }
    if JSON.is_match(&plain) {
        return Some(FollowUp::Json);
    }
    let (keep, pairs) = request::glossary(&message);
    if keep.is_empty() && pairs.is_empty() && !only_settings(&message) {
        return None;
    }
    let base = Job {
        as_json: false,
        ..stored.cloned().unwrap_or_default()
    };
    Some(FollowUp::Retranslate(Box::new(request::apply_instruction(
        base, &message,
    ))))
}

fn only_settings(message: &str) -> bool {
    if languages::find(message).0.is_none() && register::parse(message).is_none() {
        return false;
    }
    let names_gone = languages::strip_names(message);
    let registers_gone = REGISTER_WORDS.replace_all(&names_gone, " ");
    let rest = FILLER.replace_all(&registers_gone, " ");
    PUNCT
        .replace_all(&rest, " ")
        .split_whitespace()
        .next()
        .is_none()
}
