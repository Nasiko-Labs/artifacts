//! The code checks: the guarantee, not the prompt.
//!
//! An item survives only when its snippet is found in the source and every number, date word, email and
//! URL in its text is in the source too. An owner not written near the snippet becomes "Not stated"; a
//! due not written near it is blanked. Then dedup, section caps and word caps.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::{Captures, Regex};
use serde_json::{Map, Value};

use crate::grounding as g;
use crate::items::{Item, Kind, NOT_STATED, Summary};
use crate::util::{char_len, collapse_ws, re, take_chars};

pub const STATUSES: &[&str] = &["ok", "off_topic", "not_summarizable"];
pub const SECTIONS: [(&str, Kind); 3] = [
    ("tldr", Kind::Tldr),
    ("decisions", Kind::Decision),
    ("actions", Kind::Action),
];
pub const SNIPPET_MAX_CHARS: usize = 200;
pub const FIELD_MAX_CHARS: usize = 2_000;
const GENERIC_OWNERS: &[&str] = &[
    "we",
    "us",
    "our",
    "ours",
    "team",
    "the team",
    "everyone",
    "everybody",
    "all",
    "all of us",
    "they",
    "them",
    "someone",
    "somebody",
    "anyone",
    "i",
    "me",
    "you",
    "tbd",
    "tbc",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "unassigned",
    "not stated",
    "nobody",
];
const CLAUSE_ENDS: &[&str] = &[". ", "; ", ", ", " - ", ": "];

static IMAGE: LazyLock<Regex> = LazyLock::new(|| re(r"!\[[^\]]*\]\([^)]*\)"));
static LINK: LazyLock<Regex> = LazyLock::new(|| re(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)"));
static HTML: LazyLock<Regex> = LazyLock::new(|| re(r"<[^>]{1,500}>"));

pub fn word_cap(kind: Kind) -> usize {
    match kind {
        Kind::Tldr | Kind::Decision => 30,
        Kind::Action => 25,
    }
}

pub fn section_cap(kind: Kind) -> usize {
    match kind {
        Kind::Tldr => 5,
        Kind::Decision => 10,
        Kind::Action => 15,
    }
}

/// The model's summary JSON, shape-checked.
#[derive(Debug, Clone, PartialEq)]
pub struct Parsed {
    pub status: String,
    pub sections: [Vec<Map<String, Value>>; 3],
}

/// None when the JSON does not match the contract (triggers one repair).
pub fn parse_summary(data: &Value) -> Option<Parsed> {
    let object = data.as_object()?;
    let status = object.get("status").and_then(Value::as_str)?;
    if !STATUSES.contains(&status) {
        return None;
    }
    let mut sections: [Vec<Map<String, Value>>; 3] = Default::default();
    for (slot, (key, _)) in sections.iter_mut().zip(SECTIONS) {
        let entries = match object.get(key) {
            None | Some(Value::Null) => continue,
            Some(Value::Array(entries)) => entries,
            Some(_) => return None,
        };
        for entry in entries {
            slot.push(entry.as_object()?.clone());
        }
    }
    Some(Parsed {
        status: status.to_string(),
        sections,
    })
}

/// A JSON value as text, the way Python's `str(x or "")` shows it.
pub fn value_text(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) if n.as_f64() == Some(0.0) => String::new(),
        Some(other) => other.to_string(),
    }
}

/// One line of model text with images and HTML removed; a link stays only if its URL is in the source.
pub fn clean_text(text: &str, source: &str) -> String {
    let value = IMAGE
        .replace_all(take_chars(text, FIELD_MAX_CHARS), "")
        .into_owned();
    let value = LINK
        .replace_all(&value, |caps: &Captures| {
            let url = caps.get(2).map_or("", |m| m.as_str());
            if !url.is_empty() && source.contains(url) {
                caps.get(0).map_or("", |m| m.as_str()).to_string()
            } else {
                caps.get(1).map_or("", |m| m.as_str()).to_string()
            }
        })
        .into_owned();
    let value = HTML.replace_all(&value, "");
    collapse_ws(&value.replace('|', "/"))
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Checked {
    pub items: Vec<Item>,
    pub dropped: usize,
}

pub fn check_section(
    kind: Kind,
    entries: &[Map<String, Value>],
    source: &str,
    norm_source: &str,
    prefix: &str,
) -> Checked {
    let mut out = Checked::default();
    for (index, entry) in entries.iter().enumerate() {
        let id = if prefix.is_empty() {
            String::new()
        } else {
            format!("{prefix}{}{}", kind.letter(), index + 1)
        };
        match check_item(kind, entry, source, norm_source, &id) {
            Some(item) => out.items.push(item),
            None => out.dropped += 1,
        }
    }
    out
}

pub fn check_item(
    kind: Kind,
    entry: &Map<String, Value>,
    source: &str,
    norm_source: &str,
    item_id: &str,
) -> Option<Item> {
    let text = clean_text(&value_text(entry.get("text")), source);
    let snippet = collapse_ws(&value_text(entry.get("snippet")));
    if text.is_empty() {
        return None;
    }
    let span = g::locate(&snippet, norm_source)?;
    if !g::ungrounded_tokens(&text, norm_source).is_empty() {
        return None;
    }
    let (mut owner, mut due) = (String::new(), String::new());
    if kind == Kind::Action {
        let named = clean_text(&value_text(entry.get("owner")), source);
        owner = grounded_owner(&named, norm_source, span);
        let when = clean_text(&value_text(entry.get("due")), source);
        due = grounded_due(&when, norm_source, span);
    }
    Some(Item {
        kind,
        text: cut_words(&text, word_cap(kind)),
        snippet: cap_snippet(&snippet),
        owner,
        due,
        item_id: item_id.to_string(),
    })
}

pub fn grounded_owner(owner: &str, norm_source: &str, span: (usize, usize)) -> String {
    let lowered = owner.to_lowercase();
    let plain = lowered.trim_matches([' ', '.']);
    if owner.is_empty() || GENERIC_OWNERS.contains(&plain) {
        return NOT_STATED.to_string();
    }
    if g::near(owner, norm_source, span, g::OWNER_WINDOW_CHARS, 0) {
        owner.to_string()
    } else {
        NOT_STATED.to_string()
    }
}

pub fn grounded_due(due: &str, norm_source: &str, span: (usize, usize)) -> String {
    let lowered = due.to_lowercase();
    if due.is_empty() || ["null", "none", "n/a", "not stated"].contains(&lowered.as_str()) {
        return String::new();
    }
    let window = g::DUE_WINDOW_CHARS;
    if g::near(due, norm_source, span, window, window) {
        due.to_string()
    } else {
        String::new()
    }
}

/// At most `cap` words, cut back to the last clause boundary in the second half, then "…".
pub fn cut_words(text: &str, cap: usize) -> String {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() <= cap {
        return text.to_string();
    }
    let mut head = words[..cap].join(" ");
    let half = char_len(&head) / 2;
    let cut = CLAUSE_ENDS.iter().filter_map(|mark| head.rfind(mark)).max();
    if let Some(cut) = cut.filter(|&c| char_len(&head[..c]) >= half) {
        head = head[..cut].to_string();
    }
    format!("{}…", head.trim_end_matches([' ', ',', ';', ':', '-', '.']))
}

pub fn cap_snippet(snippet: &str) -> String {
    if char_len(snippet) <= SNIPPET_MAX_CHARS {
        return snippet.to_string();
    }
    format!("{}…", take_chars(snippet, SNIPPET_MAX_CHARS).trim_end())
}

/// Dedup each section, then cap it; the counts of both kinds of loss are kept for the reply.
pub fn finalize(
    tldr: Vec<Item>,
    decisions: Vec<Item>,
    actions: Vec<Item>,
    dropped: usize,
    notes: Vec<String>,
) -> Summary {
    let mut capped = 0;
    let mut cap = |items: Vec<Item>, kind: Kind| {
        let unique = dedup(items);
        let limit = section_cap(kind);
        capped += unique.len().saturating_sub(limit);
        unique.into_iter().take(limit).collect::<Vec<_>>()
    };
    let tldr = cap(tldr, Kind::Tldr);
    let decisions = cap(decisions, Kind::Decision);
    let actions = cap(actions, Kind::Action);
    Summary {
        tldr,
        decisions,
        actions,
        dropped,
        capped,
        notes,
    }
}

pub fn dedup(items: Vec<Item>) -> Vec<Item> {
    let mut seen: HashSet<(String, String)> = HashSet::new();
    items
        .into_iter()
        .filter(|item| {
            let text = g::normalise(&item.text);
            let key = (
                text.trim_matches([' ', '.', '…']).to_string(),
                g::normalise(&item.owner),
            );
            seen.insert(key)
        })
        .collect()
}

/// (tldr, decisions, actions), each checked against one source or chunk.
pub fn check_summary(parsed: &Parsed, source: &str, prefix: &str) -> [Checked; 3] {
    let norm = g::normalise(source);
    let [t, d, a] = &parsed.sections;
    [
        check_section(Kind::Tldr, t, source, &norm, prefix),
        check_section(Kind::Decision, d, source, &norm, prefix),
        check_section(Kind::Action, a, source, &norm, prefix),
    ]
}

pub fn checked_summary(parsed: &Parsed, source: &str, notes: Vec<String>) -> Summary {
    let [t, d, a] = check_summary(parsed, source, "");
    let dropped = t.dropped + d.dropped + a.dropped;
    finalize(t.items, d.items, a.items, dropped, notes)
}
