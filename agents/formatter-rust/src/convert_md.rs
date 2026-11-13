//! Pure: Markdown -> Slack mrkdwn and Markdown -> email HTML, both in code.
//!
//! Slack: `**b**` -> `*b*`, `*i*` -> `_i_`, `# H` -> `*H*`, `[t](u)` -> `<u|t>` (http/https/mailto only),
//! lists -> `•`, tables -> an aligned code block. HTML: inline-styled elements only. Images and raw HTML
//! are removed first (md_blocks::sanitize); every text run is escaped for its target.

use crate::escape;
use crate::md_blocks::{self, Block, BlockKind, INLINE};

pub const CODE_STYLE: &str = "font-family:SFMono-Regular,Menlo,Consolas,monospace;background:#f6f8fa;padding:1px 4px;border-radius:3px;";
pub const PRE_STYLE: &str = "font-family:SFMono-Regular,Menlo,Consolas,monospace;background:#f6f8fa;padding:8px 12px;margin:0 0 12px;white-space:pre-wrap;font-size:13px;";
pub const LINK_STYLE: &str = "color:#0969da;";
pub const CELL_STYLE: &str =
    "border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top;";
pub const P_STYLE: &str = "margin:0 0 12px;line-height:1.5;";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Out {
    Slack,
    Html,
}

// --- inline ---------------------------------------------------------------------------------

fn inline(text: &str, target: Out) -> String {
    let mut out = String::new();
    let mut pos = 0;
    for found in INLINE.captures_iter(text) {
        // A regex runtime error (backtrack limit) leaves the rest of the text as plain text.
        let Ok(caps) = found else { break };
        let Some(whole) = caps.get(0) else { continue };
        out.push_str(&plain(&text[pos..whole.start()], target));
        out.push_str(&token(&caps, target));
        pos = whole.end();
    }
    out.push_str(&plain(&text[pos..], target));
    out
}

fn plain(text: &str, target: Out) -> String {
    match target {
        Out::Slack => escape::slack_entities(text),
        Out::Html => escape::html_text(text),
    }
}

fn token(caps: &fancy_regex::Captures, target: Out) -> String {
    let slack = target == Out::Slack;
    let group = |name: &str| caps.name(name).map(|m| m.as_str());
    if group("code").is_some_and(|c| !c.is_empty()) {
        let body = plain(group("ctext").unwrap_or_default(), target);
        return if slack {
            format!("`{body}`")
        } else {
            format!("<code style=\"{CODE_STYLE}\">{body}</code>")
        };
    }
    if let Some(label) = group("label") {
        return link(
            group("url").unwrap_or_default(),
            &inline(label, target),
            target,
        );
    }
    if let Some(auto) = group("auto").filter(|a| !a.is_empty()) {
        return link(auto, &plain(auto, target), target);
    }
    let marks = [
        ("b1", "*", "strong"),
        ("b2", "*", "strong"),
        ("s", "~", "s"),
        ("i1", "_", "em"),
        ("i2", "_", "em"),
    ];
    for (name, slack_mark, tag) in marks {
        if let Some(inner) = group(name) {
            let inner = inline(inner, target);
            return if slack {
                format!("{slack_mark}{inner}{slack_mark}")
            } else {
                format!("<{tag}>{inner}</{tag}>")
            };
        }
    }
    plain(caps.get(0).map_or("", |m| m.as_str()), target)
}

fn link(url: &str, label: &str, target: Out) -> String {
    if !escape::safe_url(url) {
        return label.to_owned(); // an unsafe scheme is never linked; its text stays
    }
    match target {
        Out::Slack => format!("<{}|{label}>", escape::slack_entities(url)),
        Out::Html => format!(
            "<a href=\"{}\" rel=\"noopener\" style=\"{LINK_STYLE}\">{label}</a>",
            escape::html_text(url)
        ),
    }
}

// --- Slack ----------------------------------------------------------------------------------

pub fn to_mrkdwn(markdown: &str) -> String {
    md_blocks::parse(&md_blocks::sanitize(markdown))
        .iter()
        .map(mrkdwn_block)
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn mrkdwn_block(block: &Block) -> String {
    let each = |prefix: &dyn Fn(usize) -> String| {
        block
            .lines
            .iter()
            .enumerate()
            .map(|(n, x)| format!("{}{}", prefix(n + 1), inline(x, Out::Slack)))
            .collect::<Vec<_>>()
            .join("\n")
    };
    match block.kind {
        BlockKind::Heading => format!("*{}*", inline(first_line(block), Out::Slack)),
        BlockKind::Ul => each(&|_| "• ".to_owned()),
        BlockKind::Ol => each(&|n| format!("{n}. ")),
        BlockKind::Quote => each(&|_| "> ".to_owned()),
        BlockKind::Code => format!(
            "```\n{}\n```",
            escape::slack_entities(&block.lines.join("\n"))
        ),
        BlockKind::Table => format!(
            "```\n{}\n```",
            escape::slack_entities(&aligned(&block.rows))
        ),
        BlockKind::Rule => "———".to_owned(),
        BlockKind::Para => each(&|_| String::new()),
    }
}

fn first_line(block: &Block) -> &str {
    block.lines.first().map_or("", String::as_str)
}

/// A table as aligned plain text: header, a `-+-` rule, then the rows.
pub fn aligned(rows: &[Vec<String>]) -> String {
    let width = rows.iter().map(Vec::len).max().unwrap_or(0);
    let cell = |r: &Vec<String>, c: usize| r.get(c).cloned().unwrap_or_default();
    let sizes: Vec<usize> = (0..width)
        .map(|c| {
            rows.iter()
                .map(|r| cell(r, c).chars().count())
                .max()
                .unwrap_or(0)
        })
        .collect();
    let lines: Vec<String> = rows
        .iter()
        .map(|r| {
            (0..width)
                .map(|c| {
                    let text = cell(r, c);
                    let pad = sizes[c].saturating_sub(text.chars().count());
                    format!("{text}{}", " ".repeat(pad))
                })
                .collect::<Vec<_>>()
                .join(" | ")
                .trim_end()
                .to_owned()
        })
        .collect();
    let rule = sizes
        .iter()
        .map(|s| "-".repeat(*s))
        .collect::<Vec<_>>()
        .join("-+-");
    let mut out = Vec::with_capacity(lines.len() + 1);
    out.push(lines.first().cloned().unwrap_or_default());
    out.push(rule);
    out.extend(lines.iter().skip(1).cloned());
    out.join("\n")
}

// --- HTML -----------------------------------------------------------------------------------

pub fn to_html(markdown: &str) -> String {
    md_blocks::parse(&md_blocks::sanitize(markdown))
        .iter()
        .map(html_block)
        .collect::<Vec<_>>()
        .join("\n")
}

fn html_block(block: &Block) -> String {
    let joined = |sep: &str| {
        block
            .lines
            .iter()
            .map(|x| inline(x, Out::Html))
            .collect::<Vec<_>>()
            .join(sep)
    };
    match block.kind {
        BlockKind::Heading => {
            let size = match block.level {
                1 => 22,
                2 => 18,
                3 => 16,
                _ => 15,
            };
            format!(
                "<h3 style=\"margin:16px 0 8px;font-size:{size}px;\">{}</h3>",
                inline(first_line(block), Out::Html)
            )
        }
        BlockKind::Ul | BlockKind::Ol => {
            let tag = if block.kind == BlockKind::Ul {
                "ul"
            } else {
                "ol"
            };
            let items: String = block
                .lines
                .iter()
                .map(|x| {
                    format!(
                        "<li style=\"margin:0 0 4px;\">{}</li>",
                        inline(x, Out::Html)
                    )
                })
                .collect();
            format!("<{tag} style=\"margin:0 0 12px;padding-left:24px;\">{items}</{tag}>")
        }
        BlockKind::Quote => format!(
            "<blockquote style=\"margin:0 0 12px;padding-left:12px;border-left:3px solid #d0d7de;color:#57606a;\">{}</blockquote>",
            joined("<br>")
        ),
        BlockKind::Code => format!(
            "<pre style=\"{PRE_STYLE}\">{}</pre>",
            escape::html_text(&block.lines.join("\n"))
        ),
        BlockKind::Table => {
            let head: Vec<String> = block.rows.first().cloned().unwrap_or_default();
            let rows: Vec<Vec<String>> = block.rows.iter().skip(1).cloned().collect();
            let render = |c: &str| inline(c, Out::Html);
            table_html(&head, &rows, &render)
        }
        BlockKind::Rule => {
            "<hr style=\"border:none;border-top:1px solid #d0d7de;margin:16px 0;\">".to_owned()
        }
        BlockKind::Para => format!("<p style=\"{P_STYLE}\">{}</p>", joined("<br>")),
    }
}

/// A bordered table; `render_cell` turns each header and cell into markup.
pub fn table_html(
    head: &[String],
    rows: &[Vec<String>],
    render_cell: &dyn Fn(&str) -> String,
) -> String {
    let header: String = head
        .iter()
        .map(|h| {
            format!(
                "<th style=\"{CELL_STYLE}background:#f6f8fa;\">{}</th>",
                render_cell(h)
            )
        })
        .collect();
    let body: String = rows
        .iter()
        .map(|row| {
            let cells: String = row
                .iter()
                .map(|c| format!("<td style=\"{CELL_STYLE}\">{}</td>", render_cell(c)))
                .collect();
            format!("<tr>{cells}</tr>")
        })
        .collect();
    format!(
        "<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" \
style=\"border-collapse:collapse;width:100%;margin:0 0 12px;font-size:14px;\"><tr>{header}</tr>{body}</table>"
    )
}

#[cfg(test)]
pub mod tests_support {
    pub const SOURCE: &str = "# Weekly update
We shipped **search** and *filters*. See [the doc](https://example.com/doc) or [bad](javascript:x).

- one
- two

1. first
2. second

> quoted & <b>raw</b>

| Name | Owner |
| --- | --- |
| Search | Lee |

```
code <here>
```
![chart](https://x/i.png)";
}

#[cfg(test)]
mod tests {
    use super::tests_support::SOURCE;
    use super::*;

    #[test]
    fn markdown_to_mrkdwn() {
        let out = to_mrkdwn(SOURCE);
        for want in [
            "*Weekly update*",
            "*search*",
            "_filters_",
            "<https://example.com/doc|the doc>",
            "bad",
        ] {
            assert!(out.contains(want), "{want}\n{out}");
        }
        assert!(!out.contains("javascript") && !out.contains("chart"));
        assert!(
            out.contains("• one") && out.contains("1. first") && out.contains("> quoted &amp; raw")
        );
        assert!(
            out.contains("```\nName   | Owner\n-------+------\nSearch | Lee\n```"),
            "{out}"
        );
        assert!(out.contains("code &lt;here&gt;"));
    }

    #[test]
    fn markdown_to_html() {
        let out = to_html(SOURCE);
        assert!(out.contains("<strong>search</strong>") && out.contains("<em>filters</em>"));
        assert!(
            out.contains("<a href=\"https://example.com/doc\" rel=\"noopener\"")
                && !out.contains("javascript")
        );
        for tag in ["<ul", "<ol", "<blockquote", "<table"] {
            assert!(out.contains(tag), "{tag}");
        }
        assert!(out.contains("code &lt;here&gt;") && !out.contains("<img") && !out.contains("<b>"));
    }
}
