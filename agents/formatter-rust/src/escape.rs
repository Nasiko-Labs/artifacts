//! Pure: escaping for each target, the matching un-escaping (used only by the fidelity check), and the
//! link rule: only http, https and mailto URLs found in the data are ever linked.

use std::sync::LazyLock;

use regex::Regex;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static MD_SPECIALS: LazyLock<Regex> = LazyLock::new(|| re(r"([\\`*_\[\]<>|~])"));
static MD_LINE_START: LazyLock<Regex> = LazyLock::new(|| re(r"(?m)^(\s*)(#)"));
static MD_UNESCAPE: LazyLock<Regex> = LazyLock::new(|| re(r"\\([!-/:-@\[-`{-~])"));
static SAFE_URL: LazyLock<Regex> =
    LazyLock::new(|| re(r#"(?i)^(?:https?://[^\s<>"'|`]+|mailto:[^\s<>"'|`]+)$"#));
static SLACK_FORMATTING: LazyLock<Regex> = LazyLock::new(|| re(r"[*_~]"));
static TAG: LazyLock<Regex> = LazyLock::new(|| re(r"<[^>]*>"));
static BACKTICK_RUN: LazyLock<Regex> = LazyLock::new(|| re(r"`+"));
static ENTITY: LazyLock<Regex> =
    LazyLock::new(|| re(r"&(?:#([0-9]+);?|#[xX]([0-9a-fA-F]+);?|(amp|lt|gt|quot|apos|nbsp);)"));

/// True when the whole value is one http(s)/mailto URL that can be linked as is.
pub fn safe_url(value: &str) -> bool {
    value == value.trim() && SAFE_URL.is_match(value)
}

// --- Markdown ---------------------------------------------------------------------------------

pub fn md(text: &str) -> String {
    let escaped = MD_SPECIALS.replace_all(text, r"\$1");
    MD_LINE_START
        .replace_all(&escaped, r"${1}\${2}")
        .into_owned()
}

/// Escaped and on one line (bullets, table cells, key/value lines): newlines become '; '.
pub fn md_inline(text: &str) -> String {
    if !text.contains('\n') {
        return md(text);
    }
    let joined: Vec<&str> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    md(&joined.join("; "))
}

/// An inline code span that holds any text (the fence is longer than any backtick run inside).
pub fn md_code(text: &str, in_table: bool) -> String {
    let fence = "`".repeat(longest_backtick_run(text, 1) + 1);
    let body = if in_table {
        text.replace('|', "\\|")
    } else {
        text.to_owned()
    };
    format!("{fence} {body} {fence}")
}

pub fn md_unescape(text: &str) -> String {
    MD_UNESCAPE.replace_all(text, "$1").into_owned()
}

/// The length of the longest run of backticks in `text`.
pub fn longest_backtick_run(text: &str, min_run: usize) -> usize {
    BACKTICK_RUN
        .find_iter(text)
        .map(|m| m.as_str().len())
        .filter(|&n| n >= min_run)
        .max()
        .unwrap_or(0)
}

// --- Slack mrkdwn -----------------------------------------------------------------------------

/// Slack's three control characters. Applied to all data text, so `<!channel>`, `<@U1>` or
/// `<https://x|y>` in data can never ping anyone or disguise a link.
pub fn slack_entities(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Data text for mrkdwn: entities escaped; text with formatting characters goes in a code span so
/// it renders literally (a backtick inside makes that impossible; the text is then left as is).
pub fn slack(text: &str) -> String {
    let escaped = slack_entities(text);
    if SLACK_FORMATTING.is_match(text) && !text.contains('`') && !text.contains('\n') {
        return format!("`{escaped}`");
    }
    escaped
}

pub fn slack_unescape(text: &str) -> String {
    text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
}

// --- HTML -------------------------------------------------------------------------------------

pub fn html_text(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Tags become newlines, then entities are decoded in one pass.
pub fn html_unescape(markup: &str) -> String {
    let text = TAG.replace_all(markup, "\n");
    ENTITY
        .replace_all(&text, |caps: &regex::Captures| {
            let decoded = if let Some(dec) = caps.get(1) {
                dec.as_str().parse::<u32>().ok().and_then(char::from_u32)
            } else if let Some(hex) = caps.get(2) {
                u32::from_str_radix(hex.as_str(), 16)
                    .ok()
                    .and_then(char::from_u32)
            } else {
                match caps.get(3).map(|m| m.as_str()) {
                    Some("amp") => Some('&'),
                    Some("lt") => Some('<'),
                    Some("gt") => Some('>'),
                    Some("quot") => Some('"'),
                    Some("apos") => Some('\''),
                    Some("nbsp") => Some('\u{a0}'),
                    _ => None,
                }
            };
            decoded.map_or_else(|| caps[0].to_owned(), String::from)
        })
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markdown_specials_and_heading_marks_are_escaped() {
        assert_eq!(md("*a* _b_ `c` <d> |"), r"\*a\* \_b\_ \`c\` \<d\> \|");
        assert_eq!(md("# Title"), r"\# Title");
        assert_eq!(md_unescape(&md("*a* # b")), "*a* # b");
    }

    #[test]
    fn inline_joins_lines() {
        assert_eq!(md_inline("line1\n\n line2 "), "line1; line2");
    }

    #[test]
    fn code_span_fence_outgrows_backticks() {
        assert_eq!(md_code("a ``b`` c", false), "``` a ``b`` c ```");
        assert_eq!(md_code("a|b", true), r"` a\|b `");
    }

    #[test]
    fn safe_urls_only() {
        assert!(safe_url("https://example.com/a_b?x=1&y=2") && safe_url("mailto:a@example.com"));
        assert!(
            !safe_url("javascript:alert(1)")
                && !safe_url(" https://x.io")
                && !safe_url("see https://x.io")
        );
    }

    #[test]
    fn slack_escaping() {
        assert_eq!(slack("<!channel> & x"), "&lt;!channel&gt; &amp; x");
        assert_eq!(slack("*not bold*"), "`*not bold*`");
        assert_eq!(slack("a `b` *c*"), "a `b` *c*");
        assert_eq!(slack_unescape(&slack_entities("<a & b>")), "<a & b>");
    }

    #[test]
    fn html_round_trip() {
        let raw = "x<script>'\"&amp;";
        assert_eq!(html_text(raw), "x&lt;script&gt;&#x27;&quot;&amp;amp;");
        assert_eq!(
            html_unescape(&format!("<p>{}</p>", html_text(raw))),
            format!("\n{raw}\n")
        );
    }
}
