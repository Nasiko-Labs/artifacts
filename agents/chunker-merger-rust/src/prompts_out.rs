//! Pure: the text of each chunk prompt and the merge instruction. The part's text is fenced; fence
//! markers inside the pasted text are stripped so a part can never close its own fence.

use std::sync::LazyLock;

use regex::Regex;

use crate::replies::thousands;

pub const DEFAULT_STEP: &str =
    "Process this part of a longer text. Parts are numbered; return the same schema for each part.";
pub const MERGE_INSTRUCTION: &str = "When every part has run, paste each part's JSON result back to the Chunker + Merger with \
'merge these' (fenced json blocks, a JSON array, or objects separated by ---); it merges \
them into one result with the same schema.";
static MARKERS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"<<<[A-Z_]{1,40}>>>|>>>END_[A-Z_]{1,40}<<<").expect("valid regex")
});

/// (text without fence markers, how many were removed).
pub fn strip_markers(text: &str) -> (String, usize) {
    let count = MARKERS.find_iter(text).count();
    (MARKERS.replace_all(text, "").into_owned(), count)
}

pub fn chunk_prompt(
    n: usize,
    total: usize,
    label: &str,
    step: &str,
    body: &str,
    overlap_chars: usize,
) -> String {
    let mut links = vec![label.to_owned()];
    if n > 1 {
        links.push(format!("continues from part {}", n - 1));
    }
    if n < total {
        links.push(format!("continues into part {}", n + 1));
    }
    let step = strip_markers(step).0;
    let step = if step.trim().is_empty() {
        DEFAULT_STEP.to_owned()
    } else {
        step.trim().to_owned()
    };
    let mut lines = vec![
        format!("=== PART {n}/{total} ==="),
        step,
        format!("Part {n} of {total} ({}).", links.join("; ")),
    ];
    if overlap_chars > 0 {
        lines.push(format!(
            "The first {} characters repeat the end of part {} for context.",
            thousands(overlap_chars as u64),
            n - 1
        ));
    }
    lines.extend([
        "<<<TEXT>>>".to_owned(),
        body.to_owned(),
        ">>>END_TEXT<<<".to_owned(),
    ]);
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markers_are_stripped_and_counted() {
        assert_eq!(
            strip_markers("a <<<TEXT>>> b >>>END_TEXT<<< c"),
            ("a  b  c".to_owned(), 2)
        );
    }

    #[test]
    fn prompt_shape() {
        let prompt = chunk_prompt(2, 3, "turns 4-6", "", "body", 1200);
        assert_eq!(
            prompt,
            format!(
                "=== PART 2/3 ===\n{DEFAULT_STEP}\nPart 2 of 3 (turns 4-6; continues from part 1; continues into part 3).\nThe first 1,200 characters repeat the end of part 1 for context.\n<<<TEXT>>>\nbody\n>>>END_TEXT<<<"
            )
        );
    }
}
