//! Pure: the email HTML target. One table-based, inline-styled body (600 px container, system fonts, no
//! <style>, no images, scripts, iframes, forms or event attributes). Every data string is HTML-escaped
//! (quotes included); only whole-value http(s)/mailto URLs from the data become links.

use serde_json::Value;

use crate::cells::{NONE_MARK, WARNING, doc_heading, footer_parts, json_text, scalar_text};
use crate::convert_md::{self, CODE_STYLE, LINK_STYLE, P_STYLE, PRE_STYLE, table_html};
use crate::escape::html_text;
use crate::model::{Body, Card, Cell, Document, Options, Section, Stats, header};

const FONT: &str = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";
const H1_STYLE: &str = "margin:0 0 12px;font-size:22px;line-height:1.3;";
const H2_STYLE: &str = "margin:20px 0 8px;font-size:18px;line-height:1.3;";
const WARN_STYLE: &str =
    "margin:0 0 12px;padding:8px 12px;background:#fff8c5;border:1px solid #d4a72c;";
const FOOTER_STYLE: &str = "margin:20px 0 0;color:#6e7781;font-size:12px;";
const LIST_STYLE: &str = "margin:0 0 12px;padding-left:24px;";

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
        Cell::Json(v) => format!(
            "<code style=\"{CODE_STYLE}\">{}</code>",
            html_text(&json_text(v, false))
        ),
        Cell::Scalar(Value::String(s)) if crate::escape::safe_url(s) => {
            let url = html_text(s);
            format!("<a href=\"{url}\" rel=\"noopener\" style=\"{LINK_STYLE}\">{url}</a>")
        }
        Cell::Scalar(Value::String(s)) => s
            .split('\n')
            .map(html_text)
            .collect::<Vec<_>>()
            .join("<br>"),
        Cell::Scalar(other) => html_text(&scalar_text(other)),
    }
}

pub fn render(docs: &[Document], opts: &Options) -> (String, Stats) {
    let mut body: Vec<String> = Vec::new();
    if !opts.title.is_empty() {
        body.push(format!(
            "<h1 style=\"{H1_STYLE}\">{}</h1>",
            html_text(&opts.title)
        ));
    }
    for (index, doc) in docs.iter().enumerate() {
        if docs.len() > 1 {
            if index > 0 {
                body.push(
                    "<hr style=\"border:none;border-top:1px solid #d0d7de;margin:20px 0;\">"
                        .to_owned(),
                );
            }
            body.push(format!(
                "<h2 style=\"{H2_STYLE}\">{}</h2>",
                html_text(&doc_heading(doc, index + 1))
            ));
        }
        body.extend(document(doc, opts, docs.len() == 1));
    }
    (page(&body.join("\n")), Stats::default())
}

/// The 600 px, table-based, inline-styled container around the body markup.
pub fn page(inner: &str) -> String {
    format!(
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#ffffff;\">\
<tr><td align=\"center\">\n\
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" \
style=\"max-width:600px;width:100%;font-family:{FONT};font-size:15px;color:#1f2328;\">\
<tr><td style=\"padding:16px 24px;\">\n{inner}\n</td></tr></table>\n</td></tr></table>"
    )
}

fn document(doc: &Document, opts: &Options, with_lead: bool) -> Vec<String> {
    let mut parts = Vec::new();
    if doc.needs_human {
        parts.push(format!(
            "<p style=\"{WARN_STYLE}\"><strong>{WARNING}</strong></p>"
        ));
    }
    if let Some(lead) = doc.lead.as_deref().filter(|l| with_lead && !l.is_empty()) {
        parts.push(format!(
            "<p style=\"{P_STYLE}\">{}</p>",
            cell(&Cell::Scalar(Value::String(lead.trim().to_owned())))
        ));
    }
    parts.extend(doc.sections.iter().map(section));
    let footer = footer_parts(doc);
    if !footer.is_empty() && opts.footer {
        let line = footer
            .iter()
            .map(|p| html_text(p))
            .collect::<Vec<_>>()
            .join(" · ");
        parts.push(format!("<p style=\"{FOOTER_STYLE}\">{line}</p>"));
    }
    parts
}

fn section(section: &Section) -> String {
    format!(
        "<h2 style=\"{H2_STYLE}\">{}</h2>\n{}",
        html_text(&section.title),
        body(section)
    )
}

fn label(key: &str) -> String {
    format!("<strong>{}:</strong>", html_text(&header(key)))
}

fn list_of(lines: &[(String, Cell)]) -> String {
    let items: String = lines
        .iter()
        .map(|(k, v)| format!("<li>{} {}</li>", label(k), cell(v)))
        .collect();
    format!("<ul style=\"{LIST_STYLE}\">{items}</ul>")
}

fn body(section: &Section) -> String {
    match &section.body {
        Body::Paragraph {
            text: Value::String(text),
            markdown: true,
        } => convert_md::to_html(text),
        Body::Paragraph { text, .. } => format!(
            "<p style=\"{P_STYLE}\">{}</p>",
            cell(&Cell::Scalar(text.clone()))
        ),
        Body::Bullets(items) => {
            let lis: String = items
                .iter()
                .map(|v| format!("<li>{}</li>", cell(v)))
                .collect();
            let lis = if lis.is_empty() {
                "<li>—</li>".to_owned()
            } else {
                lis
            };
            format!("<ul style=\"{LIST_STYLE}\">{lis}</ul>")
        }
        Body::Fields(lines) => list_of(lines),
        Body::Cards(cards) => format!(
            "<ol style=\"{LIST_STYLE}\">{}</ol>",
            cards.iter().map(card).collect::<String>()
        ),
        Body::JsonBlock(value) => format!(
            "<pre style=\"{PRE_STYLE}\">{}</pre>",
            html_text(&json_text(value, true))
        ),
        Body::Table { columns, rows } => {
            let head: Vec<String> = columns.iter().map(|c| html_text(&header(c))).collect();
            let cells: Vec<Vec<String>> = rows
                .iter()
                .map(|row| row.iter().map(cell).collect())
                .collect();
            let as_is = |markup: &str| markup.to_owned();
            table_html(&head, &cells, &as_is)
        }
    }
}

fn card(card: &Card) -> String {
    let title = card
        .title
        .as_ref()
        .map(|t| format!("<strong>{}</strong>", cell(&Cell::Scalar(t.clone()))))
        .unwrap_or_default();
    format!(
        "<li style=\"margin:0 0 8px;\">{title}{}</li>",
        list_of(&card.lines)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::envelope::{Rendering, render_values};
    use crate::fixtures;
    use crate::model::Target;
    use crate::output;
    use regex::Regex;

    fn email(value: Value) -> Rendering {
        render_values(
            &[value],
            &Options {
                target: Target::Email,
                ..Options::default()
            },
        )
    }

    #[test]
    fn everything_from_data_is_escaped_and_nothing_active_is_emitted() {
        let forbidden = Regex::new(
            r"(?i)<\s*(script|img|iframe|form|input|style|link|object|embed)\b|\son\w+\s*=",
        )
        .expect("regex");
        for value in [
            fixtures::hostile(),
            fixtures::thread_untangler(),
            fixtures::memo(),
            fixtures::pre_mortem(),
        ] {
            let markup = email(value).formatted;
            assert!(!forbidden.is_match(&markup), "{markup}");
        }
        let markup = email(fixtures::hostile()).formatted;
        assert!(
            markup.contains("x&lt;script&gt;alert(1)&lt;/script&gt;")
                && markup.contains("&lt;!channel&gt; &amp;")
        );
    }

    #[test]
    fn only_safe_urls_are_linked_with_noopener() {
        let markup = email(fixtures::hostile()).formatted;
        assert!(
            markup.contains("<a href=\"https://example.com/a_b?x=1&amp;y=2\" rel=\"noopener\"")
        );
        assert!(markup.contains("<a href=\"mailto:a@example.com\" rel=\"noopener\""));
        assert!(!markup.contains("href=\"javascript") && markup.contains("javascript:alert(1)"));
    }

    #[test]
    fn layout_is_a_600px_inline_styled_table() {
        let markup = email(fixtures::thread_untangler()).formatted;
        for want in [
            "width=\"600\"",
            "max-width:600px",
            "padding:6px 8px",
            "border:1px solid",
            "color:#6e7781",
        ] {
            assert!(markup.contains(want), "{want}");
        }
        assert!(!markup.contains("<style"));
    }

    #[test]
    fn plain_text_alternative_and_subject() {
        let result = email(fixtures::thread_untangler());
        let plain = result.plain_text.clone().expect("a plain-text version");
        assert!(!plain.contains('<') && plain.contains("Update the pricing page"));
        assert!(
            result
                .subject
                .as_deref()
                .is_some_and(|s| s.chars().count() <= 80)
        );
        let long = email(serde_json::json!({"summary": "word ".repeat(40), "a": 1}));
        assert!(
            long.subject
                .as_deref()
                .is_some_and(|s| s.chars().count() <= 80)
        );
    }

    #[test]
    fn chat_shows_html_in_a_fence_with_the_subject() {
        let text = output::reply(&email(fixtures::thread_untangler()), false, false).text;
        assert!(text.starts_with("Suggested subject: ") && text.contains("```html\n<table"));
        let plain = output::reply(&email(fixtures::thread_untangler()), false, true).text;
        assert!(plain.starts_with("Plain-text version:") && plain.contains("```text"));
    }
}
