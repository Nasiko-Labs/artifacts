//! Pure: size limits (Markdown table cells, Slack Block Kit) and the cuts that enforce them.
//!
//! A cut always ends at a word boundary with an ellipsis; the caller records the original in `Stats` so
//! the fidelity check knows it was left out on purpose and the reply says how many were shortened.
//! All lengths are in characters, not bytes.

pub const ELLIPSIS: &str = "…";
pub const MD_CELL_CHARS: usize = 300;
pub const SLACK_HEADER_CHARS: usize = 150;
pub const SLACK_SECTION_CHARS: usize = 3000;
pub const SLACK_FIELD_CHARS: usize = 2000;
pub const SLACK_FIELDS_PER_BLOCK: usize = 10;
pub const SLACK_MAX_BLOCKS: usize = 50;
pub const SUBJECT_CHARS: usize = 80;

pub fn char_len(text: &str) -> usize {
    text.chars().count()
}

/// The first `n` characters of `text`.
pub fn take_chars(text: &str, n: usize) -> &str {
    match text.char_indices().nth(n) {
        Some((idx, _)) => &text[..idx],
        None => text,
    }
}

/// `text` when it fits, else the longest word-boundary prefix plus an ellipsis, within `limit`.
pub fn cut(text: &str, limit: usize) -> String {
    if char_len(text) <= limit {
        return text.to_owned();
    }
    let head = take_chars(text, limit.saturating_sub(char_len(ELLIPSIS)));
    let head = match head.rfind(' ') {
        Some(space) if char_len(&head[..space]) >= limit / 2 => &head[..space],
        _ => head,
    };
    format!("{}{ELLIPSIS}", head.trim_end())
}

/// Splits at line boundaries into chunks of at most `limit` characters; a single over-long line is split
/// at spaces (or hard, as a last resort). Concatenating the chunks with newlines/spaces gives the text.
pub fn split_text(text: &str, limit: usize) -> Vec<String> {
    let mut chunks: Vec<String> = Vec::new();
    let mut current = String::new();
    for line in text.split('\n') {
        for piece in split_line(line, limit) {
            let candidate = if current.is_empty() {
                piece.clone()
            } else {
                format!("{current}\n{piece}")
            };
            if char_len(&candidate) <= limit {
                current = candidate;
                continue;
            }
            if !current.is_empty() {
                chunks.push(current);
            }
            current = piece;
        }
    }
    if !current.is_empty() || chunks.is_empty() {
        chunks.push(current);
    }
    chunks
}

fn split_line(line: &str, limit: usize) -> Vec<String> {
    let mut pieces = Vec::new();
    let mut rest = line;
    while char_len(rest) > limit {
        let window = take_chars(rest, limit);
        match window.rfind(' ').filter(|&space| space > 0) {
            Some(space) => {
                pieces.push(rest[..space].to_owned());
                rest = rest[space..].trim_start_matches(' ');
            }
            None => {
                pieces.push(window.to_owned());
                rest = &rest[window.len()..];
            }
        }
    }
    pieces.push(rest.to_owned());
    pieces
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cut_keeps_short_text_and_ends_long_text_at_a_word() {
        assert_eq!(cut("short", 10), "short");
        let out = cut("alpha beta gamma delta", 12);
        assert_eq!(out, "alpha beta…");
        assert!(char_len(&out) <= 12);
    }

    #[test]
    fn cut_counts_characters_not_bytes() {
        let out = cut(&"é".repeat(20), 10);
        assert_eq!(char_len(&out), 10);
    }

    #[test]
    fn split_text_respects_the_limit_and_keeps_every_line() {
        let text = (0..10)
            .map(|n| format!("line {n} {}", "x".repeat(20)))
            .collect::<Vec<_>>()
            .join("\n");
        let chunks = split_text(&text, 70);
        assert!(chunks.len() > 1 && chunks.iter().all(|c| char_len(c) <= 70));
        assert_eq!(chunks.join("\n"), text);
    }

    #[test]
    fn split_text_splits_one_long_line_hard_when_needed() {
        let chunks = split_text(&"y".repeat(25), 10);
        assert_eq!(chunks, vec!["y".repeat(10), "y".repeat(10), "y".repeat(5)]);
        assert_eq!(split_text("", 10), vec![String::new()]);
    }
}
