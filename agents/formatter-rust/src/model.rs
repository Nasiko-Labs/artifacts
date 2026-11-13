//! Pure: the format-neutral document every renderer draws from.
//!
//! A `Document` is the input JSON laid out as titled sections (a paragraph, bullets, a table, numbered
//! cards, key/value lines, or a JSON block for very deep values). It holds the original scalar values
//! untouched: renderers escape them for their target and never change them.

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Target {
    Email,
    Markdown,
    Slack,
}

impl Target {
    pub fn name(self) -> &'static str {
        match self {
            Target::Markdown => "markdown",
            Target::Slack => "slack",
            Target::Email => "email",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Target::Markdown => "Markdown",
            Target::Slack => "Slack blocks",
            Target::Email => "email HTML",
        }
    }
}

/// One value in a cell, a bullet or a key/value line.
#[derive(Debug, Clone, PartialEq)]
pub enum Cell {
    /// A scalar as given (string, number, boolean or null).
    Scalar(Value),
    /// A short list of scalars.
    List(Vec<Value>),
    /// A value too nested for its place, shown as inline JSON.
    Json(Value),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Card {
    /// A non-null scalar, or None.
    pub title: Option<Value>,
    pub lines: Vec<(String, Cell)>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Body {
    Paragraph {
        text: Value,
        markdown: bool,
    },
    Bullets(Vec<Cell>),
    Table {
        columns: Vec<String>,
        rows: Vec<Vec<Cell>>,
    },
    Cards(Vec<Card>),
    Fields(Vec<(String, Cell)>),
    JsonBlock(Value),
}

/// A section's layout, for tests that check shape detection.
#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Paragraph,
    Bullets,
    Table,
    Cards,
    Fields,
    JsonBlock,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Section {
    pub title: String,
    pub body: Body,
}

impl Section {
    pub fn new(title: impl Into<String>, body: Body) -> Self {
        Self {
            title: title.into(),
            body,
        }
    }

    #[cfg(test)]
    pub fn kind(&self) -> Kind {
        match self.body {
            Body::Paragraph { .. } => Kind::Paragraph,
            Body::Bullets(_) => Kind::Bullets,
            Body::Table { .. } => Kind::Table,
            Body::Cards(_) => Kind::Cards,
            Body::Fields(_) => Kind::Fields,
            Body::JsonBlock(_) => Kind::JsonBlock,
        }
    }

    pub fn size(&self) -> usize {
        match &self.body {
            Body::Bullets(items) => items.len(),
            Body::Table { rows, .. } => rows.len(),
            Body::Cards(cards) => cards.len(),
            Body::Fields(lines) => lines.len(),
            Body::Paragraph { .. } | Body::JsonBlock(_) => 1,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Document {
    pub sections: Vec<Section>,
    /// The envelope's summary.
    pub lead: Option<String>,
    /// (confidence, needs_human) as given (null when absent), None when neither was given.
    pub footer: Option<(Value, Value)>,
    pub needs_human: bool,
    /// None = the generic renderer guessed the shape.
    pub template: Option<&'static str>,
    /// Fields nested deeper than the limit, shown as JSON.
    pub deep: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Options {
    pub target: Target,
    pub compact: bool,
    pub footer: bool,
    pub title: String,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            target: Target::Markdown,
            compact: false,
            footer: true,
            title: String::new(),
        }
    }
}

/// What one render lost or reshaped, for the notes, the confidence and the fidelity check.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Stats {
    pub cut: usize,
    pub collapsed: usize,
    pub blocks: usize,
    /// Original text left out on purpose (cut, collapsed).
    pub excluded: Vec<String>,
}

pub fn is_scalar(value: &Value) -> bool {
    !matches!(value, Value::Array(_) | Value::Object(_))
}

const HEADER_OVERRIDES: [(&str, &str); 6] = [
    ("to_whom", "To"),
    ("source_quote", "Quote"),
    ("days_overdue", "Days overdue"),
    ("alt_shorter", "Shorter version"),
    ("memo_markdown", "Memo"),
    ("tldr", "TL;DR"),
];

/// `days_overdue` -> `Days overdue`; a few keys have a fixed heading.
pub fn header(key: &str) -> String {
    if let Some((_, fixed)) = HEADER_OVERRIDES.iter().find(|(k, _)| *k == key) {
        return (*fixed).to_owned();
    }
    let words = key.replace('_', " ");
    let words = words.trim();
    let mut chars = words.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).collect(),
        None => "Value".to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn headers() {
        assert_eq!(header("days_overdue"), "Days overdue");
        assert_eq!(header("to_whom"), "To");
        assert_eq!(header("_"), "Value");
        assert_eq!(header("owner_name"), "Owner name");
    }
}
