//! The code checks: the guarantee, not the prompt.
//!
//! Every topic bullet, decision and action survives only when its quote is found (filler-tolerant) in the
//! turn(s) it cites; a quote found in exactly one other turn is re-pointed there, in several or none it is
//! dropped. Every number, date word, email and URL in its own words must be in the transcript too. Owners,
//! dues and kinds go through `owners.rs`. Then dedup and caps.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::{Captures, Regex};
use serde_json::{Map, Value};

use crate::grounding as g;
use crate::items::{Action, Bullet, Decision, Kind, Topic, Turn};
use crate::owners;
use crate::util::{char_len, collapse_ws, re, take_chars};

pub const STATUSES: &[&str] = &["ok", "off_topic", "not_meeting"];
pub const TITLE_WORDS: usize = 8;
pub const BULLET_WORDS: usize = 25;
pub const DECISION_WORDS: usize = 30;
pub const ACTION_WORDS: usize = 25;
pub const MAX_TOPICS: usize = 8;
pub const MAX_BULLETS: usize = 5;
pub const MAX_DECISIONS: usize = 15;
pub const MAX_ACTIONS: usize = 25;
pub const MAX_CITED_TURNS: usize = 3;
pub const QUOTE_MAX_CHARS: usize = 200;
pub const FIELD_MAX_CHARS: usize = 2_000;
const CLAUSE_ENDS: &[&str] = &[". ", "; ", ", ", " - ", ": "];

static IMAGE: LazyLock<Regex> = LazyLock::new(|| re(r"!\[[^\]]*\]\([^)]*\)"));
static LINK: LazyLock<Regex> = LazyLock::new(|| re(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)"));
static HTML: LazyLock<Regex> = LazyLock::new(|| re(r"<[^>]{1,500}>"));

/// The parsed turns, normalised once for every check of one turn.
#[derive(Debug, Clone)]
pub struct Index {
    pub turns: Vec<Turn>,
    pub speakers: Vec<String>,
    pub by_id: HashMap<String, usize>,
    pub norms: Vec<String>,
    pub source: String,
    pub norm_source: String,
}

impl Index {
    pub fn new(turns: &[Turn], speakers: &[String]) -> Self {
        let source = turns
            .iter()
            .map(|t| format!("{} {}: {}", t.time, t.speaker, t.text))
            .collect::<Vec<_>>()
            .join("\n");
        Self {
            turns: turns.to_vec(),
            speakers: speakers.to_vec(),
            by_id: turns
                .iter()
                .enumerate()
                .map(|(i, t)| (t.id.clone(), i))
                .collect(),
            norms: turns.iter().map(|t| g::normalise(&t.text)).collect(),
            norm_source: g::normalise(&source),
            source,
        }
    }
}

/// The model's notes JSON, shape-checked.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ParsedNotes {
    pub status: String,
    pub topics: Vec<Map<String, Value>>,
    pub decisions: Vec<Map<String, Value>>,
    pub actions: Vec<Map<String, Value>>,
}

/// A JSON value Python would treat as "empty" in `data.get(key) or []`.
fn falsy(value: &Value) -> bool {
    match value {
        Value::Null | Value::Bool(false) => true,
        Value::String(s) => s.is_empty(),
        Value::Number(n) => n.as_f64() == Some(0.0),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
        Value::Bool(true) => false,
    }
}

/// A list of objects under `key` (missing or empty = none); None when it is anything else.
pub fn object_list(object: &Map<String, Value>, key: &str) -> Option<Vec<Map<String, Value>>> {
    match object.get(key) {
        None => Some(Vec::new()),
        Some(v) if falsy(v) => Some(Vec::new()),
        Some(Value::Array(items)) => items.iter().map(|x| x.as_object().cloned()).collect(),
        Some(_) => None,
    }
}

/// None when the JSON does not match the contract (one repair follows).
pub fn parse_notes(data: &Value) -> Option<ParsedNotes> {
    let object = data.as_object()?;
    let status = object.get("status").and_then(Value::as_str)?;
    if !STATUSES.contains(&status) {
        return None;
    }
    Some(ParsedNotes {
        status: status.to_string(),
        topics: object_list(object, "topics")?,
        decisions: object_list(object, "decisions")?,
        actions: object_list(object, "actions")?,
    })
}

/// A JSON value as text, the way Python's `str(x or "")` shows it.
pub fn value_text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) => s.clone(),
        Some(v) if !falsy(v) => v.to_string(),
        _ => String::new(),
    }
}

/// One line of model text without images or HTML; a link stays only if its URL is in the source.
pub fn clean_text(text: &str, source: &str) -> String {
    let value = IMAGE
        .replace_all(take_chars(text, FIELD_MAX_CHARS), "")
        .into_owned();
    let value = LINK
        .replace_all(&value, |caps: &Captures| {
            let url = caps.get(2).map_or("", |m| m.as_str());
            let keep = if !url.is_empty() && source.contains(url) {
                0
            } else {
                1
            };
            caps.get(keep).map_or("", |m| m.as_str()).to_string()
        })
        .into_owned();
    let value = HTML.replace_all(&value, "");
    collapse_ws(&value.replace('|', "/"))
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
        head.truncate(cut);
    }
    format!("{}…", head.trim_end_matches([' ', ',', ';', ':', '-', '.']))
}

pub fn cap_quote(quote: &str) -> String {
    let quote = collapse_ws(quote);
    if char_len(&quote) <= QUOTE_MAX_CHARS {
        return quote;
    }
    format!("{}…", take_chars(&quote, QUOTE_MAX_CHARS).trim_end())
}

/// The turn ids the quote is in: the cited ones when it is there, else the one other turn holding it.
pub fn find_quote(quote: &str, cited: &[String], index: &Index) -> Option<Vec<String>> {
    let known: Vec<String> = cited
        .iter()
        .filter(|c| index.by_id.contains_key(*c))
        .take(MAX_CITED_TURNS)
        .cloned()
        .collect();
    if !known.is_empty() {
        let joined = known
            .iter()
            .filter_map(|c| index.by_id.get(c).map(|&i| index.norms[i].as_str()))
            .collect::<Vec<_>>()
            .join(" ");
        if g::locate(quote, &joined).is_some() {
            return Some(known);
        }
    }
    let hits: Vec<&Turn> = index
        .turns
        .iter()
        .zip(&index.norms)
        .filter(|(_, norm)| g::locate(quote, norm).is_some())
        .map(|(t, _)| t)
        .collect();
    (hits.len() == 1).then(|| vec![hits[0].id.clone()])
}

pub fn grounded_text(raw: Option<&Value>, cap: usize, index: &Index) -> String {
    let text = clean_text(&value_text(raw), &index.source);
    if text.is_empty() || !g::ungrounded_tokens(&text, &index.norm_source).is_empty() {
        return String::new();
    }
    cut_words(&text, cap)
}

fn cited_one(entry: &Map<String, Value>) -> Vec<String> {
    vec![value_text(entry.get("turn"))]
}

pub fn check_decision(
    entry: &Map<String, Value>,
    index: &Index,
    item_id: &str,
) -> Option<Decision> {
    let text = grounded_text(entry.get("text"), DECISION_WORDS, index);
    let quote = value_text(entry.get("quote"));
    if text.is_empty() {
        return None;
    }
    let found = find_quote(&quote, &cited_one(entry), index)?;
    Some(Decision {
        text,
        turn: found[0].clone(),
        quote: cap_quote(&quote),
        item_id: item_id.to_string(),
    })
}

pub fn check_action(entry: &Map<String, Value>, index: &Index, item_id: &str) -> Option<Action> {
    let task = grounded_text(entry.get("task"), ACTION_WORDS, index);
    let quote = value_text(entry.get("quote"));
    if task.is_empty() {
        return None;
    }
    let found = find_quote(&quote, &cited_one(entry), index)?;
    let at = *index.by_id.get(&found[0])?;
    let owner_name = clean_text(&value_text(entry.get("owner_name")), &index.source);
    let owner_ref = entry.get("owner_ref").and_then(Value::as_str);
    let (owner, accepted) = owners::resolve(
        owner_ref,
        &owner_name,
        &quote,
        at,
        &index.turns,
        &index.speakers,
    );
    let kind = if accepted == Some(false) {
        Kind::FollowUp
    } else {
        owners::kind_of(entry.get("kind").and_then(Value::as_str), &task)
    };
    let due_text = clean_text(&value_text(entry.get("due")), &index.source);
    let due = owners::grounded_due(&due_text, &quote, at, &index.turns);
    Some(Action {
        task,
        kind,
        owner,
        due,
        turn: found[0].clone(),
        quote: cap_quote(&quote),
        item_id: item_id.to_string(),
    })
}

fn id_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|ids| {
            ids.iter()
                .map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// (topic or None, bullets dropped).
pub fn check_topic(
    entry: &Map<String, Value>,
    index: &Index,
    item_id: &str,
) -> (Option<Topic>, usize) {
    let title = grounded_text(entry.get("title"), TITLE_WORDS, index);
    let raw: Vec<&Map<String, Value>> = entry
        .get("bullets")
        .and_then(Value::as_array)
        .map(|b| b.iter().filter_map(Value::as_object).collect())
        .unwrap_or_default();
    let (mut bullets, mut dropped) = (Vec::new(), 0);
    for (n, b) in raw.into_iter().enumerate() {
        let text = grounded_text(b.get("text"), BULLET_WORDS, index);
        let snippet = value_text(b.get("snippet"));
        let found = if text.is_empty() {
            None
        } else {
            find_quote(&snippet, &id_list(b.get("turns")), index)
        };
        match found {
            Some(turns) => bullets.push(Bullet {
                text,
                turns,
                snippet: cap_quote(&snippet),
                item_id: if item_id.is_empty() {
                    String::new()
                } else {
                    format!("{item_id}.{}", n + 1)
                },
            }),
            None => dropped += 1,
        }
    }
    if title.is_empty() || bullets.is_empty() {
        let lost = if title.is_empty() { bullets.len() } else { 0 };
        return (None, dropped + lost);
    }
    bullets.truncate(MAX_BULLETS);
    let topic = Topic {
        title,
        bullets,
        item_id: item_id.to_string(),
    };
    (Some(topic), dropped)
}

/// What survived the checks of one model answer.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Checked {
    pub topics: Vec<Topic>,
    pub decisions: Vec<Decision>,
    pub actions: Vec<Action>,
    pub dropped: usize,
}

impl Checked {
    pub fn kept(&self) -> usize {
        self.topics.iter().map(|t| t.bullets.len()).sum::<usize>()
            + self.decisions.len()
            + self.actions.len()
    }
}

pub fn check_all(parsed: &ParsedNotes, index: &Index, prefix: &str) -> Checked {
    let ident = |kind: &str, n: usize| {
        if prefix.is_empty() {
            String::new()
        } else {
            format!("{prefix}{kind}{n}")
        }
    };
    let mut out = Checked::default();
    for (n, entry) in parsed.topics.iter().enumerate() {
        let (topic, lost) = check_topic(entry, index, &ident("t", n + 1));
        out.dropped += lost;
        out.topics.extend(topic);
    }
    for (n, entry) in parsed.decisions.iter().enumerate() {
        match check_decision(entry, index, &ident("d", n + 1)) {
            Some(d) => out.decisions.push(d),
            None => out.dropped += 1,
        }
    }
    for (n, entry) in parsed.actions.iter().enumerate() {
        match check_action(entry, index, &ident("a", n + 1)) {
            Some(a) => out.actions.push(a),
            None => out.dropped += 1,
        }
    }
    out
}

/// Keep the first of each (normalised text, normalised owner).
pub fn dedup<T>(
    items: Vec<T>,
    text_of: impl Fn(&T) -> &str,
    owner_of: impl Fn(&T) -> &str,
) -> Vec<T> {
    let mut seen: HashSet<(String, String)> = HashSet::new();
    items
        .into_iter()
        .filter(|item| {
            let text = g::normalise(text_of(item));
            let key = (
                text.trim_matches([' ', '.', '…']).to_string(),
                g::normalise(owner_of(item)),
            );
            seen.insert(key)
        })
        .collect()
}

/// Dedup and cap every section; `capped` counts what the caps cut.
pub fn finalize(
    topics: Vec<Topic>,
    decisions: Vec<Decision>,
    actions: Vec<Action>,
) -> (Vec<Topic>, Vec<Decision>, Vec<Action>, usize) {
    let mut t = dedup(topics, |x| &x.title, |_| "");
    let mut d = dedup(decisions, |x| &x.text, |_| "");
    let mut a = dedup(actions, |x| &x.task, |x| &x.owner);
    let capped = t.len().saturating_sub(MAX_TOPICS)
        + d.len().saturating_sub(MAX_DECISIONS)
        + a.len().saturating_sub(MAX_ACTIONS);
    t.truncate(MAX_TOPICS);
    d.truncate(MAX_DECISIONS);
    a.truncate(MAX_ACTIONS);
    (t, d, a, capped)
}
