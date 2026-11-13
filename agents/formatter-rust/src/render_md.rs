//! Pure: the Markdown target (and, with `plain = true`, the unformatted text used as the email's
//! plain-text alternative). GitHub tables with escaped cells; cells over 300 chars are cut (counted, and
//! kept whole in compact mode, which uses bullets instead of tables).

use serde_json::Value;

use crate::cells::{NONE_MARK, WARNING, doc_heading, footer_parts, json_text, scalar_text};
use crate::escape;
use crate::limits::{MD_CELL_CHARS, cut};
use crate::md_blocks;
use crate::model::{Body, Card, Cell, Document, Options, Section, Stats, header};

struct Style {
    plain: bool,
    stats: Stats,
}

impl Style {
    fn bold(&self, text: &str) -> String {
        if self.plain {
            text.to_owned()
        } else {
            format!("**{text}**")
        }
    }

    fn esc(&self, text: &str) -> String {
        if self.plain {
            text.to_owned()
        } else {
            escape::md_inline(text)
        }
    }

    fn label(&self, key: &str) -> String {
        self.bold(&format!("{}:", self.esc(&header(key))))
    }

    fn heading(&self, text: &str, level: usize) -> String {
        if self.plain {
            let rule = "-".repeat(text.chars().count().min(60));
            return format!("{text}\n{rule}");
        }
        format!("{} {}", "#".repeat(level), escape::md_inline(text))
    }

    fn cell(&mut self, value: &Cell, limit: Option<usize>, in_table: bool) -> String {
        match value {
            Cell::List(items) => {
                let parts: Vec<String> = items
                    .iter()
                    .map(|v| self.cell(&Cell::Scalar(v.clone()), limit, in_table))
                    .collect();
                if parts.is_empty() {
                    NONE_MARK.to_owned()
                } else {
                    parts.join(", ")
                }
            }
            Cell::Json(v) => {
                let text = self.limited(&json_text(v, false), limit);
                if self.plain {
                    text
                } else {
                    escape::md_code(&text, in_table)
                }
            }
            Cell::Scalar(Value::String(s)) => {
                let text = self.limited(s, limit);
                if self.plain {
                    text.split_whitespace().collect::<Vec<_>>().join(" ")
                } else if escape::safe_url(&text) {
                    format!("<{text}>")
                } else {
                    escape::md_inline(&text)
                }
            }
            Cell::Scalar(other) => scalar_text(other),
        }
    }

    fn limited(&mut self, text: &str, limit: Option<usize>) -> String {
        match limit {
            Some(limit) if text.chars().count() > limit => {
                self.stats.cut += 1;
                self.stats.excluded.push(text.to_owned());
                cut(text, limit)
            }
            _ => text.to_owned(),
        }
    }
}

pub fn render(docs: &[Document], opts: &Options, plain: bool) -> (String, Stats) {
    let mut style = Style {
        plain,
        stats: Stats::default(),
    };
    let compact = opts.compact || plain;
    let mut parts: Vec<String> = Vec::new();
    if !opts.title.is_empty() {
        parts.push(style.heading(&opts.title, 1));
    }
    let several = docs.len() > 1;
    for (index, doc) in docs.iter().enumerate() {
        if several {
            if index > 0 {
                parts.push(if plain {
                    "-".repeat(20)
                } else {
                    "---".to_owned()
                });
            }
            parts.push(style.heading(&doc_heading(doc, index + 1), 2));
        }
        let level = if several { 3 } else { 2 };
        parts.extend(document(doc, &mut style, opts, compact, level, !several));
    }
    let text = parts
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");
    (text, style.stats)
}

fn document(
    doc: &Document,
    style: &mut Style,
    opts: &Options,
    compact: bool,
    level: usize,
    with_lead: bool,
) -> Vec<String> {
    let mut parts = Vec::new();
    if doc.needs_human {
        parts.push(style.bold(WARNING));
    }
    if let Some(lead) = doc.lead.as_deref().filter(|l| with_lead && !l.is_empty()) {
        parts.push(paragraph(lead, style));
    }
    for section in &doc.sections {
        parts.push(section_text(section, style, compact, level));
    }
    let footer = footer_parts(doc);
    if !footer.is_empty() && opts.footer {
        let line = footer
            .iter()
            .map(|p| style.esc(p))
            .collect::<Vec<_>>()
            .join(" · ");
        parts.push(if style.plain {
            line
        } else {
            format!("_{line}_")
        });
    }
    parts
}

fn paragraph(text: &str, style: &Style) -> String {
    if style.plain {
        return text.trim().to_owned();
    }
    text.trim()
        .split('\n')
        .map(escape::md)
        .collect::<Vec<_>>()
        .join("  \n")
}

fn section_text(section: &Section, style: &mut Style, compact: bool, level: usize) -> String {
    format!(
        "{}\n\n{}",
        style.heading(&section.title, level),
        body(section, style, compact)
    )
}

fn body(section: &Section, style: &mut Style, compact: bool) -> String {
    match &section.body {
        Body::Paragraph {
            text: Value::String(text),
            markdown: true,
        } => {
            let source = md_blocks::sanitize(text);
            if style.plain {
                md_blocks::plain_words_source(&source).trim().to_owned()
            } else {
                source.trim().to_owned()
            }
        }
        Body::Paragraph {
            text: Value::String(text),
            ..
        } => paragraph(text, style),
        Body::Paragraph { text, .. } => style.cell(&Cell::Scalar(text.clone()), None, false),
        Body::Bullets(items) => {
            let lines: Vec<String> = items
                .iter()
                .map(|item| format!("- {}", style.cell(item, None, false)))
                .collect();
            if lines.is_empty() {
                NONE_MARK.to_owned()
            } else {
                lines.join("\n")
            }
        }
        Body::Fields(lines) => lines
            .iter()
            .map(|(k, v)| format!("- {} {}", style.label(k), style.cell(v, None, false)))
            .collect::<Vec<_>>()
            .join("\n"),
        Body::Cards(cards) => cards
            .iter()
            .enumerate()
            .map(|(n, c)| card(n + 1, c, style))
            .collect::<Vec<_>>()
            .join("\n"),
        Body::JsonBlock(value) => {
            let text = json_text(value, true);
            if style.plain {
                text
            } else {
                format!("```json\n{text}\n```")
            }
        }
        Body::Table { columns, rows } if compact => compact_table(columns, rows, style),
        Body::Table { columns, rows } => table(columns, rows, style),
    }
}

fn card(n: usize, card: &Card, style: &mut Style) -> String {
    let mut lines: Vec<String> = card
        .lines
        .iter()
        .map(|(k, v)| format!("   - {} {}", style.label(k), style.cell(v, None, false)))
        .collect();
    let mut title = match &card.title {
        Some(t) => {
            let text = style.cell(&Cell::Scalar(t.clone()), None, false);
            style.bold(&text)
        }
        None => String::new(),
    };
    if title.is_empty() && !lines.is_empty() {
        let first = lines.remove(0);
        title = first.trim().chars().skip(2).collect();
    }
    let head = format!("{n}. {title}").trim_end().to_owned();
    std::iter::once(head)
        .chain(lines)
        .collect::<Vec<_>>()
        .join("\n")
}

fn table(columns: &[String], rows: &[Vec<Cell>], style: &mut Style) -> String {
    let head = format!(
        "| {} |",
        columns
            .iter()
            .map(|c| style.esc(&header(c)))
            .collect::<Vec<_>>()
            .join(" | ")
    );
    let rule = format!("| {} |", vec!["---"; columns.len()].join(" | "));
    let mut lines = vec![head, rule];
    for row in rows {
        let cells: Vec<String> = row
            .iter()
            .map(|c| style.cell(c, Some(MD_CELL_CHARS), true))
            .collect();
        lines.push(format!("| {} |", cells.join(" | ")));
    }
    lines.join("\n")
}

fn compact_table(columns: &[String], rows: &[Vec<Cell>], style: &mut Style) -> String {
    rows.iter()
        .enumerate()
        .map(|(n, row)| {
            let pairs: Vec<String> = columns
                .iter()
                .zip(row)
                .map(|(k, v)| format!("{} {}", style.label(k), style.cell(v, None, false)))
                .collect();
            format!("{}. {}", n + 1, pairs.join(" · "))
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::envelope::{Rendering, render_values};
    use crate::fixtures;
    use crate::shapes::build_document;
    use serde_json::json;

    fn md(value: Value, opts: Options) -> Rendering {
        render_values(&[value], &opts)
    }

    #[test]
    fn table_cells_are_escaped_and_newlines_become_semicolons() {
        let text = md(
            json!({"rows": [{"a": "x | y", "b": "line1\nline2", "c": "*star* _u_ `c` <b>"}]}),
            Options::default(),
        )
        .formatted;
        assert!(
            text.contains("x \\| y") && text.contains("line1; line2"),
            "{text}"
        );
        assert!(
            text.contains("\\*star\\* \\_u\\_ \\`c\\` \\<b\\>"),
            "{text}"
        );
    }

    #[test]
    fn null_boolean_number_and_date_rendering() {
        let text = md(
            json!({"rows": [{"n": null, "t": true, "f": false, "num": 1.25, "d": "2026-11-07"}]}),
            Options::default(),
        )
        .formatted;
        assert!(
            text.contains("| — | yes | no | 1.25 | 2026-11-07 |"),
            "{text}"
        );
    }

    #[test]
    fn long_cells_are_cut_and_counted_and_kept_whole_in_compact() {
        let long = "word ".repeat(100).trim().to_owned();
        let result = md(json!({"rows": [{"a": long, "b": 1}]}), Options::default());
        assert!(
            result.stats.cut == 1
                && result.formatted.contains('…')
                && result.confidence == "medium"
        );
        assert_eq!(
            result.notes,
            vec!["1 long value shortened (say \"compact\" to see them whole)"]
        );
        let compact = md(
            json!({"rows": [{"a": long, "b": 1}]}),
            Options {
                compact: true,
                ..Options::default()
            },
        );
        assert!(
            compact.formatted.contains(&long)
                && !compact.formatted.contains('|')
                && compact.stats.cut == 0
        );
    }

    #[test]
    fn footer_and_warning_and_no_footer() {
        let result = md(fixtures::promises(), Options::default());
        assert!(
            result
                .formatted
                .starts_with("**⚠ Needs a person to review**")
        );
        assert!(
            result
                .formatted
                .trim_end()
                .ends_with("_Confidence: high · Needs a person: yes_")
        );
        assert!(result.needs_human);
        let bare = md(
            fixtures::promises(),
            Options {
                footer: false,
                ..Options::default()
            },
        );
        assert!(!bare.formatted.contains("Confidence:"));
    }

    #[test]
    fn title_option_is_an_escaped_heading() {
        let opts = Options {
            title: "Q4 *plan*".into(),
            ..Options::default()
        };
        assert!(
            md(fixtures::generic(), opts)
                .formatted
                .starts_with("# Q4 \\*plan\\*")
        );
    }

    #[test]
    fn safe_links_only() {
        let text = md(fixtures::hostile(), Options::default()).formatted;
        assert!(
            text.contains("<https://example.com/a_b?x=1&y=2>")
                && text.contains("<mailto:a@example.com>")
        );
        assert!(text.contains("javascript:alert(1)") && !text.contains("<javascript:"));
    }

    #[test]
    fn markdown_field_is_passed_through_sanitised() {
        let value = json!({"memo_markdown": "## Context\n**Bold** ![img](https://x/i.png) <script>x</script>",
                           "recommendation": "B"});
        let text = md(value, Options::default()).formatted;
        assert!(
            text.contains("**Bold**") && !text.contains("![img]") && !text.contains("<script>")
        );
    }

    #[test]
    fn several_envelopes_have_headings_and_dividers() {
        let result = render_values(
            &[
                json!({"summary": "One", "a": 1}),
                json!({"summary": "Two", "b": 2}),
            ],
            &Options::default(),
        );
        assert!(result.formatted.contains("## One") && result.formatted.contains("## Two"));
        assert!(
            result.formatted.contains("\n---\n")
                && result.summary.starts_with("Formatted 2 envelopes")
        );
    }

    #[test]
    fn depth_note_and_json_block() {
        let deep = json!({"a": {"b": {"c": {"d": {"e": {"f": {"g": "leaf"}}}}}}});
        let result = md(json!({"summary": "s", "deep": deep}), Options::default());
        assert!(result.formatted.contains("```json") && result.formatted.contains("\"leaf\""));
        assert!(
            result
                .notes
                .contains(&"1 field nested deeper than 6 levels shown as JSON".to_owned())
        );
    }

    #[test]
    fn plain_text_rendering_has_no_markup() {
        let (text, _) = render(
            &[build_document(&fixtures::thread_untangler())],
            &Options::default(),
            true,
        );
        assert!(
            !text.contains("**")
                && !text.contains("| ---")
                && text.contains("Send the revised deck | v2")
        );
    }
}
