//! Pure: reads the instruction words around the JSON (target, compact, no footer, title, as json,
//! plain-text version) and decides whether a message is only a follow-up ("as slack", "compact") on the
//! input recalled from the history. Words only; no model.

use std::sync::LazyLock;

use regex::Regex;

use crate::model::{Options, Target};

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static TITLE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\btitle\s*:\s*([^\n,;]+)"));
static PLAIN_VERSION: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bplain[- ]?text(?: version| alternative)?\b|\btext version\b"));
static AS_JSON: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\b(?:as|in|to) json\b|\bjson only\b|\braw json\b|\bthe envelope\b"));
static NO_FOOTER: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\bno[- ]footer\b|\bwithout (?:the |a )?footer\b|\bdrop the footer\b")
});
static WITH_FOOTER: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bwith (?:the |a )?footer\b"));
static COMPACT: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\bcompact\b|\bbullets only\b|\bno tables?\b"));
static FULL: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\b(?:full|with tables?|not compact)\b"));
static TARGETS: LazyLock<[(Target, Regex); 3]> = LazyLock::new(|| {
    [
        (
            Target::Slack,
            re(r"(?i)\b(?:slack|block ?kit|blocks|mrkdwn)\b"),
        ),
        (Target::Email, re(r"(?i)\b(?:e-?mail|html)\b")),
        (Target::Markdown, re(r"(?i)\b(?:markdown|md|plain)\b")),
    ]
});
static FILLER: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"(?i)\b(?:as|in|to|into|for|now|please|pls|format|formatted|render|rendered|convert|converted|turn|it|this|that|show|me|the|version|make|give|again|instead|and|then|but|a|an|same|one|thing|output|do|can|you|could|would|send|of|with|without|footer|tables?|bullets?|only|no|not|full|text|alternative|kit|slack|block|blocks|mrkdwn|e-?mail|html|markdown|md|plain|compact|json|raw|envelope)\b",
    )
});
static PUNCT: LazyLock<Regex> = LazyLock::new(|| re(r"[^\w]+"));
const FOLLOWUP_MAX_CHARS: usize = 160;

/// One message's instruction words. `target` is None when none was named.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Ask {
    pub target: Option<Target>,
    pub compact: Option<bool>,
    pub footer: Option<bool>,
    pub title: Option<String>,
    pub as_json: bool,
    pub plain_version: bool,
}

pub fn read(text: &str) -> Ask {
    let title = TITLE.captures(text).map(|c| c[1].trim().to_owned());
    let rest = TITLE.replace_all(text, " ");
    let plain_version = PLAIN_VERSION.is_match(&rest);
    let target_words = PLAIN_VERSION.replace_all(&rest, " ");
    let compact = if FULL.is_match(&rest) {
        Some(false)
    } else {
        COMPACT.is_match(&rest).then_some(true)
    };
    let footer = if NO_FOOTER.is_match(&rest) {
        Some(false)
    } else {
        WITH_FOOTER.is_match(&rest).then_some(true)
    };
    Ask {
        target: if plain_version {
            Some(Target::Email)
        } else {
            target_of(&target_words)
        },
        compact,
        footer,
        title,
        as_json: AS_JSON.is_match(&rest),
        plain_version,
    }
}

/// The first target named in `text` (None when none).
pub fn target_of(text: &str) -> Option<Target> {
    TARGETS
        .iter()
        .filter_map(|(name, pattern)| pattern.find(text).map(|m| (m.start(), *name)))
        .min()
        .map(|(_, name)| name)
}

/// `base` with this message's choices on top (a choice not mentioned keeps its stored value).
pub fn apply(ask: &Ask, base: &Options) -> Options {
    Options {
        target: ask.target.unwrap_or(base.target),
        compact: ask.compact.unwrap_or(base.compact),
        footer: ask.footer.unwrap_or(base.footer),
        title: ask.title.clone().unwrap_or_else(|| base.title.clone()),
    }
}

/// True when the message is nothing but follow-up words (and names at least one choice).
pub fn is_followup_only(text: &str) -> bool {
    let stripped = text.trim();
    if stripped.is_empty() || stripped.chars().count() > FOLLOWUP_MAX_CHARS {
        return false;
    }
    let ask = read(stripped);
    let named = ask.target.is_some()
        || ask.compact.is_some()
        || ask.footer.is_some()
        || ask.title.is_some()
        || ask.as_json
        || ask.plain_version;
    let without_title = TITLE.replace_all(stripped, " ");
    let without_filler = FILLER.replace_all(&without_title, " ");
    let leftover = PUNCT.replace_all(&without_filler, " ");
    named && leftover.trim().is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_words() {
        let cases = [
            ("as slack", Some(Target::Slack)),
            ("in Block Kit", Some(Target::Slack)),
            ("as email", Some(Target::Email)),
            ("html please", Some(Target::Email)),
            ("as markdown", Some(Target::Markdown)),
            ("md", Some(Target::Markdown)),
            ("show the plain-text version", Some(Target::Email)),
            ("compact", None),
        ];
        for (text, target) in cases {
            assert_eq!(read(text).target, target, "{text}");
        }
    }

    #[test]
    fn option_words() {
        let ask = read("As markdown, compact, no footer, title: Weekly pricing");
        assert_eq!(ask.target, Some(Target::Markdown));
        assert_eq!((ask.compact, ask.footer), (Some(true), Some(false)));
        assert_eq!(ask.title.as_deref(), Some("Weekly pricing"));
        assert!(read("as json").as_json && read("json only").as_json);
        assert_eq!(read("not compact").compact, Some(false));
    }

    #[test]
    fn followup_only_messages() {
        for text in [
            "as slack",
            "now as email",
            "as markdown, compact",
            "no footer",
            "as json",
            "show the plain-text version",
            "compact please",
            "title: Q4",
        ] {
            assert!(is_followup_only(text), "{text}");
        }
    }

    #[test]
    fn not_followups() {
        for text in [
            "what's the weather?",
            "as slack the weather in Paris",
            "hello",
            "",
        ] {
            assert!(!is_followup_only(text), "{text}");
        }
    }

    #[test]
    fn apply_keeps_unmentioned_choices() {
        let base = Options {
            compact: true,
            ..Options::default()
        };
        let opts = apply(&read("as slack"), &base);
        assert_eq!(opts.target, Target::Slack);
        assert!(opts.compact && opts.footer);
    }
}
