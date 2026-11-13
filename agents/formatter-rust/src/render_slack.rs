//! Pure: the Slack Block Kit target. Output `{"blocks": [...], "text": "<plain fallback>"}`.
//!
//! All data text is entity-escaped (`&`, `<`, `>`), so data can never mention anyone or disguise a link;
//! only whole-value http(s)/mailto URLs from the data become `<url|label>` links. Limits (header 150, section
//! 3000, 10 fields of 2000, 50 blocks) are enforced in code; the tail beyond 50 blocks is collapsed into a
//! note.

use serde_json::{Value, json};

use crate::cells::{NONE_MARK, WARNING, doc_heading, footer_parts, json_text, scalar_text};
use crate::convert_md;
use crate::escape::{self, slack_entities};
use crate::limits;
use crate::model::{Body, Card, Cell, Document, Options, Section, Stats, header};

const SMALL_TABLE_ROWS: usize = 5;

pub fn cell(value: &Cell) -> String {
    match value {
        Cell::List(items) => {
            let parts: Vec<String> = items
                .iter()
                .map(|v| cell(&Cell::Scalar(v.clone())))
                .collect();
            if parts.is_empty() {
                NONE_MARK.to_owned()
            } else {
                parts.join(", ")
            }
        }
        Cell::Json(v) => format!("`{}`", slack_entities(&json_text(v, false))),
        Cell::Scalar(Value::String(s)) if escape::safe_url(s) => {
            format!("<{}|{}>", slack_entities(s), slack_entities(s))
        }
        Cell::Scalar(Value::String(s)) if s.contains('\n') => {
            escape::slack(&s.split_whitespace().collect::<Vec<_>>().join(" "))
        }
        Cell::Scalar(Value::String(s)) => escape::slack(s),
        Cell::Scalar(other) => scalar_text(other),
    }
}

/// Multi-line data text: each line escaped (and code-spanned when it has formatting characters).
fn prose(text: &str) -> String {
    text.trim()
        .split('\n')
        .map(escape::slack)
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn section(text: &str) -> Value {
    json!({"type": "section", "text": {"type": "mrkdwn", "text": text}})
}

fn context(text: &str) -> Value {
    json!({"type": "context", "elements": [{"type": "mrkdwn", "text": text}]})
}

fn divider() -> Value {
    json!({"type": "divider"})
}

pub fn render(docs: &[Document], opts: &Options) -> (Value, Stats) {
    let mut stats = Stats::default();
    let mut blocks: Vec<Value> = Vec::new();
    let first = docs.first();
    let single_lead = first
        .filter(|_| docs.len() == 1)
        .and_then(|d| d.lead.clone())
        .unwrap_or_default();
    let head = if opts.title.is_empty() {
        single_lead
    } else {
        opts.title.clone()
    };
    if !head.is_empty() {
        let joined = head.split_whitespace().collect::<Vec<_>>().join(" ");
        let text = limits::cut(&slack_entities(&joined), limits::SLACK_HEADER_CHARS);
        blocks.push(
            json!({"type": "header", "text": {"type": "plain_text", "text": text, "emoji": true}}),
        );
    }
    for (index, doc) in docs.iter().enumerate() {
        if docs.len() > 1 {
            blocks.push(divider());
            blocks.push(section(&format!(
                "*{}*",
                slack_entities(&doc_heading(doc, index + 1))
            )));
        }
        blocks.extend(document(doc, opts, &mut stats, docs.len() == 1));
    }
    let blocks = capped(blocks, &mut stats);
    stats.blocks = blocks.len();
    let lead = first.and_then(|d| d.lead.clone()).filter(|l| !l.is_empty());
    let fallback = lead
        .or_else(|| (!opts.title.is_empty()).then(|| opts.title.clone()))
        .unwrap_or_else(|| "Formatted output".to_owned());
    (
        json!({"blocks": blocks, "text": slack_entities(&fallback)}),
        stats,
    )
}

fn document(doc: &Document, opts: &Options, stats: &mut Stats, with_lead: bool) -> Vec<Value> {
    let mut blocks = Vec::new();
    if doc.needs_human {
        blocks.push(section(&format!("*{WARNING}*")));
    }
    if let Some(lead) = doc.lead.as_deref().filter(|l| with_lead && !l.is_empty()) {
        blocks.extend(sections(&prose(lead)));
    }
    for item in &doc.sections {
        blocks.push(divider());
        blocks.extend(section_blocks(item, opts, stats));
    }
    let footer = footer_parts(doc);
    if !footer.is_empty() && opts.footer {
        let text = footer
            .iter()
            .map(|p| slack_entities(p))
            .collect::<Vec<_>>()
            .join(" · ");
        blocks.push(context(&text));
    }
    blocks
}

fn sections(text: &str) -> Vec<Value> {
    limits::split_text(text, limits::SLACK_SECTION_CHARS)
        .iter()
        .map(|c| section(c))
        .collect()
}

fn section_blocks(item: &Section, opts: &Options, stats: &mut Stats) -> Vec<Value> {
    let title = format!("*{}*", slack_entities(&item.title));
    if let Body::Table { columns, rows } = &item.body
        && !opts.compact
        && rows.len() <= SMALL_TABLE_ROWS
        && columns.len() <= limits::SLACK_FIELDS_PER_BLOCK
    {
        let mut blocks = vec![section(&title)];
        blocks.extend(rows.iter().map(|row| fields_block(columns, row, stats)));
        return blocks;
    }
    sections(&format!("{title}\n{}", body(item)))
}

fn fields_block(columns: &[String], row: &[Cell], stats: &mut Stats) -> Value {
    let fields: Vec<Value> = columns
        .iter()
        .zip(row)
        .map(|(key, value)| {
            let mut text = format!("*{}*\n{}", slack_entities(&header(key)), cell(value));
            if text.chars().count() > limits::SLACK_FIELD_CHARS {
                stats.cut += 1;
                stats.excluded.push(escape::slack_unescape(&text));
                text = limits::cut(&text, limits::SLACK_FIELD_CHARS);
            }
            json!({"type": "mrkdwn", "text": text})
        })
        .collect();
    json!({"type": "section", "fields": fields})
}

fn body(item: &Section) -> String {
    match &item.body {
        Body::Paragraph {
            text: Value::String(text),
            markdown: true,
        } => convert_md::to_mrkdwn(text),
        Body::Paragraph {
            text: Value::String(text),
            ..
        } => prose(text),
        Body::Paragraph { text, .. } => cell(&Cell::Scalar(text.clone())),
        Body::Bullets(items) => {
            let lines: Vec<String> = items.iter().map(|v| format!("• {}", cell(v))).collect();
            if lines.is_empty() {
                NONE_MARK.to_owned()
            } else {
                lines.join("\n")
            }
        }
        Body::Fields(lines) => lines
            .iter()
            .map(|(k, v)| format!("*{}:* {}", slack_entities(&header(k)), cell(v)))
            .collect::<Vec<_>>()
            .join("\n"),
        Body::Cards(cards) => cards
            .iter()
            .enumerate()
            .map(|(n, c)| card(n + 1, c))
            .collect::<Vec<_>>()
            .join("\n"),
        Body::JsonBlock(value) => format!("```\n{}\n```", slack_entities(&json_text(value, true))),
        Body::Table { columns, rows } => {
            let head = columns
                .iter()
                .map(|c| format!("*{}*", slack_entities(&header(c))))
                .collect::<Vec<_>>()
                .join(" — ");
            let lines = rows.iter().enumerate().map(|(n, row)| {
                format!(
                    "*{}.* {}",
                    n + 1,
                    row.iter().map(cell).collect::<Vec<_>>().join(" — ")
                )
            });
            std::iter::once(head)
                .chain(lines)
                .collect::<Vec<_>>()
                .join("\n")
        }
    }
}

fn card(n: usize, card: &Card) -> String {
    let title = match &card.title {
        Some(t) => format!("*{n}. {}*", cell(&Cell::Scalar(t.clone()))),
        None => format!("*{n}.*"),
    };
    let lines = card
        .lines
        .iter()
        .map(|(k, v)| format!("    {}: {}", slack_entities(&header(k)), cell(v)));
    std::iter::once(title)
        .chain(lines)
        .collect::<Vec<_>>()
        .join("\n")
}

fn capped(blocks: Vec<Value>, stats: &mut Stats) -> Vec<Value> {
    if blocks.len() <= limits::SLACK_MAX_BLOCKS {
        return blocks;
    }
    let (keep, dropped) = blocks.split_at(limits::SLACK_MAX_BLOCKS - 1);
    stats.collapsed = dropped.iter().filter(|b| b["type"] != "divider").count();
    stats.excluded.extend(
        block_texts(dropped)
            .iter()
            .map(|t| escape::slack_unescape(t)),
    );
    let note = format!(
        "{} more blocks not shown; ask for compact or as markdown.",
        stats.collapsed
    );
    keep.iter()
        .cloned()
        .chain(std::iter::once(context(&note)))
        .collect()
}

/// Every text string in a list of blocks (header, section text and fields, context elements).
pub fn block_texts(blocks: &[Value]) -> Vec<String> {
    let mut texts = Vec::new();
    for block in blocks {
        if let Some(text) = block.pointer("/text/text").and_then(Value::as_str) {
            texts.push(text.to_owned());
        }
        for key in ["fields", "elements"] {
            let items = block
                .get(key)
                .and_then(Value::as_array)
                .into_iter()
                .flatten();
            texts.extend(
                items
                    .filter_map(|f| f.get("text").and_then(Value::as_str))
                    .map(str::to_owned),
            );
        }
    }
    texts
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::envelope::{Rendering, render_values};
    use crate::fixtures;
    use crate::model::Target;

    fn slack(value: Value) -> (Rendering, Value) {
        let result = render_values(
            &[value],
            &Options {
                target: Target::Slack,
                ..Options::default()
            },
        );
        let payload = serde_json::from_str(&result.formatted).expect("valid JSON");
        (result, payload)
    }

    fn joined(payload: &Value) -> String {
        block_texts(payload["blocks"].as_array().expect("blocks")).join("\n")
    }

    #[test]
    fn shape_header_sections_dividers_and_footer_context() {
        let (result, payload) = slack(fixtures::thread_untangler());
        let kinds: Vec<&str> = payload["blocks"]
            .as_array()
            .expect("blocks")
            .iter()
            .filter_map(|b| b["type"].as_str())
            .collect();
        assert!(
            kinds[0] == "header" && kinds.contains(&"divider") && kinds.last() == Some(&"context")
        );
        assert_eq!(payload["text"], fixtures::thread_untangler()["summary"]);
        assert_eq!(result.target, "slack");
        assert_eq!(result.stats.blocks, kinds.len());
    }

    #[test]
    fn header_is_cut_to_150() {
        let (_, payload) = slack(serde_json::json!({"summary": "word ".repeat(80), "a": 1}));
        let header = payload["blocks"][0]["text"]["text"]
            .as_str()
            .expect("header text");
        assert!(header.chars().count() <= limits::SLACK_HEADER_CHARS && header.ends_with('…'));
    }

    #[test]
    fn long_section_is_split_at_line_boundaries() {
        let lines: Vec<String> = (0..80)
            .map(|n| format!("Item {n}: {}", "x".repeat(90)))
            .collect();
        let (_, payload) = slack(serde_json::json!({"notes_list": lines}));
        let sections: Vec<&Value> = payload["blocks"]
            .as_array()
            .expect("blocks")
            .iter()
            .filter(|b| b["type"] == "section")
            .collect();
        assert!(sections.len() >= 3);
        assert!(sections.iter().all(|b| {
            b["text"]["text"]
                .as_str()
                .is_some_and(|t| t.chars().count() <= 3000)
        }));
    }

    #[test]
    fn small_tables_use_at_most_ten_fields() {
        let (_, payload) = slack(serde_json::json!({"rows": [{"a": 1, "b": 2, "c": 3}]}));
        let fields: Vec<&Value> = payload["blocks"]
            .as_array()
            .expect("blocks")
            .iter()
            .filter(|b| b.get("fields").is_some())
            .collect();
        assert_eq!(fields.len(), 1);
        assert_eq!(fields[0]["fields"].as_array().map(Vec::len), Some(3));
    }

    #[test]
    fn field_over_2000_is_cut_and_counted() {
        let (result, payload) = slack(serde_json::json!({"rows": [{"a": "w ".repeat(1500)}]}));
        let block = payload["blocks"]
            .as_array()
            .expect("blocks")
            .iter()
            .find(|b| b.get("fields").is_some())
            .cloned()
            .expect("a fields block");
        assert!(
            block["fields"][0]["text"]
                .as_str()
                .is_some_and(|t| t.chars().count() <= 2000)
        );
        assert!(result.stats.cut == 1 && result.confidence == "medium");
    }

    #[test]
    fn more_than_fifty_blocks_collapse_with_a_note() {
        let value: serde_json::Map<String, Value> = (0..40)
            .map(|n| {
                (
                    format!("section_{n}"),
                    serde_json::json!([format!("value {n}")]),
                )
            })
            .collect();
        let (result, payload) = slack(Value::Object(value));
        let blocks = payload["blocks"].as_array().expect("blocks");
        assert_eq!(blocks.len(), limits::SLACK_MAX_BLOCKS);
        let note = blocks
            .last()
            .and_then(|b| b["elements"][0]["text"].as_str())
            .unwrap_or_default();
        assert!(note.contains("more blocks not shown; ask for compact or as markdown"));
        assert!(result.stats.collapsed > 0 && result.confidence == "medium");
        assert!(result.notes.iter().any(|n| n.contains("collapsed")));
    }

    #[test]
    fn mentions_and_disguised_links_in_data_are_escaped() {
        let (_, payload) = slack(fixtures::hostile());
        let all = format!(
            "{}{}",
            joined(&payload),
            payload["text"].as_str().unwrap_or_default()
        );
        assert!(
            !all.contains("<!channel>")
                && !all.contains("<@U123>")
                && !all.contains("<https://evil.example|click>")
        );
        assert!(all.contains("&lt;!channel&gt;") && all.contains("&amp;"));
    }

    #[test]
    fn real_links_only_for_safe_urls() {
        let (_, payload) = slack(fixtures::hostile());
        let all = joined(&payload);
        assert!(
            all.contains("<https://example.com/a_b?x=1&amp;y=2|")
                && all.contains("<mailto:a@example.com|")
        );
        assert!(!all.contains("<javascript:") && all.contains("javascript:alert(1)"));
    }

    #[test]
    fn formatting_characters_in_data_render_literally() {
        let (_, payload) =
            slack(serde_json::json!({"rows": ["*not bold*", "snake_case_name", "plain"]}));
        let all = joined(&payload);
        assert!(
            all.contains("`*not bold*`")
                && all.contains("`snake_case_name`")
                && all.contains("• plain")
        );
    }
}
