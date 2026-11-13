//! Pure: a small Markdown reader (the subset agents and people write: headings, paragraphs, lists,
//! code fences, blockquotes, tables, rules, and inline bold/italic/strike/code/links) and the sanitiser
//! that removes images and raw HTML. Used by convert_md.rs to produce Slack mrkdwn and email HTML.

use std::sync::LazyLock;

use regex::Regex;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static FENCE: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*(```+|~~~+)\s*([\w+-]*)\s*$"));
static HEADING: LazyLock<Regex> = LazyLock::new(|| re(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$"));
static BULLET: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*[-*+]\s+(.*)$"));
static NUMBERED: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*\d+[.)]\s+(.*)$"));
static QUOTE: LazyLock<Regex> = LazyLock::new(|| re(r"^\s{0,3}>\s?(.*)$"));
static RULE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s{0,3}(?:-(?:\s*-){2,}|\*(?:\s*\*){2,}|_(?:\s*_){2,})\s*$"));
static TABLE_SEP: LazyLock<Regex> =
    LazyLock::new(|| re(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$"));
static IMAGE: LazyLock<Regex> = LazyLock::new(|| re(r"!\[([^\]]*)\]\([^)]*\)"));
static RAW_TAG: LazyLock<Regex> = LazyLock::new(|| re(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>"));
static CODE_SPAN: LazyLock<Regex> = LazyLock::new(|| re(r"`+[^`]*?`+"));
static LINK: LazyLock<Regex> = LazyLock::new(|| re(r"\[([^\]]+)\]\(([^)\s]+)\)"));

/// Inline tokens. Needs a back-reference (code fences of equal length) and look-around (intra-word
/// underscores), so it uses `fancy_regex`.
pub static INLINE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| {
    fancy_regex::Regex::new(concat!(
        r"(?P<code>`+)(?P<ctext>.+?)(?P=code)",
        r"|\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)",
        r"|<(?P<auto>(?:https?://|mailto:)[^>\s]+)>",
        r"|\*\*(?P<b1>.+?)\*\*|__(?P<b2>.+?)__",
        r"|~~(?P<s>.+?)~~",
        r"|\*(?P<i1>[^*\s](?:[^*]*[^*\s])?)\*",
        r"|(?<![\w])_(?P<i2>[^_\s](?:[^_]*[^_\s])?)_(?![\w])",
    ))
    .expect("valid regex")
});

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockKind {
    Heading,
    Para,
    Ul,
    Ol,
    Code,
    Quote,
    Table,
    Rule,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Block {
    pub kind: BlockKind,
    pub lines: Vec<String>,
    /// Heading level.
    pub level: usize,
    /// Table: header row first.
    pub rows: Vec<Vec<String>>,
}

impl Block {
    fn new(kind: BlockKind, lines: Vec<String>) -> Self {
        Self {
            kind,
            lines,
            level: 0,
            rows: Vec::new(),
        }
    }
}

/// `line` split into (is code span, piece) runs, code spans kept whole.
fn code_spans(line: &str) -> Vec<(bool, &str)> {
    let mut pieces = Vec::new();
    let mut last = 0;
    for m in CODE_SPAN.find_iter(line) {
        pieces.push((false, &line[last..m.start()]));
        pieces.push((true, m.as_str()));
        last = m.end();
    }
    pieces.push((false, &line[last..]));
    pieces
}

/// Images and raw HTML removed outside code (fences and inline spans); everything else kept.
pub fn sanitize(markdown: &str) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut in_fence = false;
    for line in markdown.split('\n') {
        if FENCE.is_match(line) {
            in_fence = !in_fence;
            lines.push(line.to_owned());
            continue;
        }
        if in_fence {
            lines.push(line.to_owned());
            continue;
        }
        let clean: String = code_spans(line)
            .into_iter()
            .map(|(code, piece)| {
                if code {
                    piece.to_owned()
                } else {
                    RAW_TAG
                        .replace_all(&IMAGE.replace_all(piece, ""), "")
                        .into_owned()
                }
            })
            .collect();
        lines.push(clean);
    }
    lines.join("\n")
}

/// The text a reader sees: sanitised, with link targets dropped (used by the fidelity check).
pub fn plain_words_source(markdown: &str) -> String {
    LINK.replace_all(&sanitize(markdown), "$1").into_owned()
}

pub fn parse(markdown: &str) -> Vec<Block> {
    let normalized = markdown.replace("\r\n", "\n");
    let lines: Vec<&str> = normalized.split('\n').collect();
    let mut blocks = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        let (block, next) = next_block(&lines, i);
        blocks.extend(block);
        i = next;
    }
    blocks
}

fn next_block(lines: &[&str], i: usize) -> (Option<Block>, usize) {
    let line = lines[i];
    if line.trim().is_empty() {
        return (None, i + 1);
    }
    if let Some(fence) = FENCE.captures(line) {
        let marker = &fence[1];
        let end = (i + 1..lines.len())
            .find(|&j| lines[j].trim().starts_with(marker))
            .unwrap_or(lines.len());
        let body = lines[i + 1..end].iter().map(|s| (*s).to_owned()).collect();
        return (Some(Block::new(BlockKind::Code, body)), end + 1);
    }
    if let Some(heading) = HEADING.captures(line) {
        let block = Block {
            level: heading[1].len(),
            ..Block::new(BlockKind::Heading, vec![heading[2].to_owned()])
        };
        return (Some(block), i + 1);
    }
    if RULE.is_match(line) {
        return (Some(Block::new(BlockKind::Rule, Vec::new())), i + 1);
    }
    if line.contains('|') && i + 1 < lines.len() && TABLE_SEP.is_match(lines[i + 1]) {
        return table(lines, i);
    }
    for (kind, pattern) in [
        (BlockKind::Ul, &*BULLET),
        (BlockKind::Ol, &*NUMBERED),
        (BlockKind::Quote, &*QUOTE),
    ] {
        if pattern.is_match(line) {
            return run(lines, i, kind, pattern);
        }
    }
    let mut end = i;
    while end < lines.len() && !lines[end].trim().is_empty() && !starts_block(lines[end]) {
        end += 1;
    }
    let end = end.max(i + 1);
    let para = lines[i..end].iter().map(|x| x.trim().to_owned()).collect();
    (Some(Block::new(BlockKind::Para, para)), end)
}

fn starts_block(line: &str) -> bool {
    [&*FENCE, &*HEADING, &*RULE, &*BULLET, &*NUMBERED, &*QUOTE]
        .iter()
        .any(|pattern| pattern.is_match(line))
}

fn run(lines: &[&str], mut i: usize, kind: BlockKind, pattern: &Regex) -> (Option<Block>, usize) {
    let mut items = Vec::new();
    while i < lines.len() {
        let Some(found) = pattern.captures(lines[i]) else {
            break;
        };
        items.push(found[1].to_owned());
        i += 1;
    }
    (Some(Block::new(kind, items)), i)
}

fn table(lines: &[&str], i: usize) -> (Option<Block>, usize) {
    let mut rows = vec![cells(lines[i])];
    let mut j = i + 2;
    while j < lines.len() && lines[j].contains('|') && !lines[j].trim().is_empty() {
        rows.push(cells(lines[j]));
        j += 1;
    }
    let block = Block {
        rows,
        ..Block::new(BlockKind::Table, Vec::new())
    };
    (Some(block), j)
}

/// A table row's cells: split on `|` not preceded by a backslash; `\|` inside a cell becomes `|`.
fn cells(line: &str) -> Vec<String> {
    let body = line.trim();
    let body = body.strip_prefix('|').unwrap_or(body);
    let body = match body.strip_suffix('|') {
        Some(head) if !body.ends_with("\\|") => head,
        _ => body,
    };
    let mut out = Vec::new();
    let mut current = String::new();
    let mut previous = '\0';
    for ch in body.chars() {
        if ch == '|' && previous != '\\' {
            out.push(std::mem::take(&mut current));
        } else {
            current.push(ch);
        }
        previous = ch;
    }
    out.push(current);
    out.into_iter()
        .map(|c| c.trim().replace("\\|", "|"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_of_each_kind() {
        let src = "# Title\npara one\npara two\n\n- a\n- b\n\n1. x\n\n> q\n\n---\n\n| A | B |\n| --- | --- |\n| 1 | 2 \\| 3 |\n\n```\ncode\n```";
        let kinds: Vec<BlockKind> = parse(src).iter().map(|b| b.kind).collect();
        use BlockKind::*;
        assert_eq!(kinds, vec![Heading, Para, Ul, Ol, Quote, Rule, Table, Code]);
        let table = parse(src)
            .into_iter()
            .find(|b| b.kind == Table)
            .expect("a table");
        assert_eq!(table.rows, vec![vec!["A", "B"], vec!["1", "2 | 3"]]);
    }

    #[test]
    fn sanitize_removes_images_and_tags_outside_code() {
        let out =
            sanitize("**B** ![i](https://x/i.png) <script>x</script> `<b>`\n```\n<b>kept</b>\n```");
        assert_eq!(out, "**B**  x `<b>`\n```\n<b>kept</b>\n```");
    }

    #[test]
    fn inline_tokens_need_lookaround() {
        let names: Vec<&str> = INLINE
            .captures_iter("snake_case _it_ **b** ``c`d``")
            .filter_map(Result::ok)
            .map(|c| {
                ["code", "i2", "b1"]
                    .into_iter()
                    .find(|n| c.name(n).is_some())
                    .unwrap_or("other")
            })
            .collect();
        assert_eq!(names, vec!["i2", "b1", "code"]);
    }
}
