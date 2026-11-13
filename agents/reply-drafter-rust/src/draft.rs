//! Pure: the model's JSON answer, parsed into a Reply (or a needs-info / off-topic status).

use serde_json::Value;

use crate::util::take_chars;

pub const SLOTS: &[&str] = &["incoming", "purpose", "channel"];
pub const MAX_ASKS: usize = 5;
pub const MAX_ASK_CHARS: usize = 300;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ask {
    pub ask: String,
    /// Verbatim from the incoming message (checked in code).
    pub quote: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Reply {
    pub subject: String,
    pub draft: String,
    pub alt_shorter: Option<String>,
    pub asks: Vec<Ask>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Answer {
    Ok(Reply),
    NeedsInfo(Vec<String>),
    OffTopic,
    Invalid,
}

pub fn parse_answer(data: Option<&Value>) -> Answer {
    let Some(Value::Object(data)) = data else {
        return Answer::Invalid;
    };
    let status = data.get("status").and_then(Value::as_str).unwrap_or("");
    if status == "off_topic" {
        return Answer::OffTopic;
    }
    if status == "needs_info" {
        let listed: Vec<&str> = data
            .get("missing")
            .and_then(Value::as_array)
            .map(|items| items.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        let mut missing: Vec<String> = SLOTS
            .iter()
            .filter(|s| listed.contains(s))
            .map(|s| s.to_string())
            .collect();
        if missing.is_empty() {
            missing.push("incoming".into());
        }
        return Answer::NeedsInfo(missing);
    }
    let draft = data.get("draft").and_then(Value::as_str).unwrap_or("");
    if status != "ok" || draft.trim().is_empty() {
        return Answer::Invalid;
    }
    let alt = text_of(data.get("alt_shorter"));
    Answer::Ok(Reply {
        subject: text_of(data.get("subject")),
        draft: draft.trim().to_string(),
        alt_shorter: (!alt.is_empty()).then_some(alt),
        asks: asks_of(data.get("asks_unaddressed")),
    })
}

fn text_of(value: Option<&Value>) -> String {
    let Some(text) = value.and_then(Value::as_str) else {
        return String::new();
    };
    let trimmed = text.trim();
    match trimmed.to_lowercase().as_str() {
        "null" | "none" => String::new(),
        _ => trimmed.to_string(),
    }
}

fn asks_of(raw: Option<&Value>) -> Vec<Ask> {
    let Some(items) = raw.and_then(Value::as_array) else {
        return Vec::new();
    };
    items
        .iter()
        .filter(|item| item.is_object())
        .map(|item| (text_of(item.get("ask")), text_of(item.get("quote"))))
        .filter(|(ask, quote)| !ask.is_empty() && !quote.is_empty())
        .map(|(ask, quote)| Ask {
            ask: take_chars(&ask, MAX_ASK_CHARS).to_string(),
            quote: take_chars(&quote, MAX_ASK_CHARS).to_string(),
        })
        .take(MAX_ASKS)
        .collect()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn answers_parse_and_bad_ones_are_invalid() {
        let ok = json!({"status": "ok", "subject": "null", "draft": " Hi ", "alt_shorter": null,
                        "asks_unaddressed": [{"ask": "a", "quote": "q"}, {"ask": "", "quote": "x"}, 3]});
        let Answer::Ok(reply) = parse_answer(Some(&ok)) else {
            panic!("ok answer");
        };
        assert_eq!((reply.subject.as_str(), reply.draft.as_str()), ("", "Hi"));
        assert_eq!(reply.alt_shorter, None);
        assert_eq!(reply.asks.len(), 1);
        assert_eq!(
            parse_answer(Some(&json!({"status": "off_topic"}))),
            Answer::OffTopic
        );
        assert_eq!(
            parse_answer(Some(
                &json!({"status": "needs_info", "missing": ["channel", "x"]})
            )),
            Answer::NeedsInfo(vec!["channel".into()])
        );
        assert_eq!(parse_answer(Some(&json!(["x"]))), Answer::Invalid);
        assert_eq!(parse_answer(None), Answer::Invalid);
        assert_eq!(
            parse_answer(Some(&json!({"status": "ok", "draft": "  "}))),
            Answer::Invalid
        );
    }
}
