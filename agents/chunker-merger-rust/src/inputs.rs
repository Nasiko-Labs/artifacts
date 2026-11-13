//! Pure: what the user gave this turn, read from the A2A message parts.
//!
//! - The user's own words (and pasted JSON) are the FIRST text part. Later text parts are org policy and
//!   agent knowledge: ignored, since there is no model to instruct.
//! - With chat history the first part is "{history}\n\nCurrent message: {text}"; the text after the last
//!   marker is this turn's request, and the history before it is parsed into turns (history.rs). This agent
//!   keeps no state: follow-ups recompute from the input found in those turns (recall.rs).
//! - A text file part (an API client can send one) is appended to the text as data.
//! - Line endings are normalised to `\n` (the split offsets and the lossless check depend on it).

use a2a::{Part, PartContent};

use crate::history::{self, Turn};

const MAX_FILE_BYTES: usize = 5 * 1024 * 1024;
const TEXT_TYPES: [&str; 4] = [
    "application/json",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
];
const TEXT_SUFFIXES: [(&str, &str); 4] = [
    (".json", "application/json"),
    (".md", "text/markdown"),
    (".markdown", "text/markdown"),
    (".txt", "text/plain"),
];

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Incoming {
    pub text: String,
    pub notes: Vec<String>,
    /// The platform's history slice: this agent's only memory.
    pub history: Vec<Turn>,
}

/// The user's text (first text part) plus any readable file parts.
pub fn read_parts(parts: &[Part]) -> Incoming {
    let mut texts: Vec<&str> = Vec::new();
    let mut files: Vec<String> = Vec::new();
    let mut notes: Vec<String> = Vec::new();
    for part in parts {
        let raw = match &part.content {
            PartContent::Text(text) => {
                texts.push(text);
                continue;
            }
            PartContent::Raw(bytes) => Some(bytes.as_slice()),
            PartContent::Url(_) => None,
            PartContent::Data(_) => continue,
        };
        let name = part.filename.as_deref().unwrap_or("the attached file");
        match decode_file(name, part.media_type.as_deref(), raw) {
            Ok(text) => files.push(text),
            Err(note) => notes.push(note),
        }
    }
    let (block, user) = texts.first().map_or(("", ""), |t| history::split(t));
    let text = std::iter::once(user.trim().to_owned())
        .chain(files)
        .filter(|p| !p.trim().is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");
    Incoming {
        text: normalise(&text),
        notes,
        history: history::parse(&normalise(block)),
    }
}

pub fn normalise(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

fn suffix(name: &str) -> String {
    let base = name.rsplit('/').next().unwrap_or(name);
    match base.rfind('.') {
        Some(idx) if idx > 0 => base[idx..].to_lowercase(),
        _ => String::new(),
    }
}

/// A readable file's text, or the one-sentence note that says why it was not read.
pub fn decode_file(
    name: &str,
    media_type: Option<&str>,
    data: Option<&[u8]>,
) -> Result<String, String> {
    let declared = media_type
        .unwrap_or_default()
        .split(';')
        .next()
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    let kind = if TEXT_TYPES.contains(&declared.as_str()) {
        declared
    } else {
        let ext = suffix(name);
        TEXT_SUFFIXES
            .iter()
            .find(|(s, _)| *s == ext)
            .map_or(declared, |(_, kind)| (*kind).to_owned())
    };
    let Some(data) = data.filter(|_| TEXT_TYPES.contains(&kind.as_str())) else {
        let shown = if kind.is_empty() {
            "unknown type"
        } else {
            kind.as_str()
        };
        return Err(format!(
            "I can't read {name} ({shown}); paste its text instead."
        ));
    };
    if data.len() > MAX_FILE_BYTES {
        return Err(format!("{name} is over 5 MB; paste a smaller part."));
    }
    Ok(String::from_utf8_lossy(data).into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_text_part_is_the_user_and_policy_parts_are_ignored() {
        let parts = vec![
            Part::text("hi"),
            Part::text("## Organization Policy\nBe formal."),
        ];
        assert_eq!(read_parts(&parts).text, "hi");
    }

    #[test]
    fn history_is_split_off_and_parsed() {
        let incoming = read_parts(&[Part::text(
            "user: x\nassistant: y\n\nCurrent message: as slack",
        )]);
        assert_eq!(incoming.text, "as slack");
        assert_eq!(
            incoming.history,
            vec![Turn::new("user", "x"), Turn::new("assistant", "y")]
        );
    }

    #[test]
    fn json_file_part_is_data_and_unknown_files_get_a_note() {
        let mut file = Part::raw(br#"{"a": 1}"#.to_vec());
        file.filename = Some("data.json".into());
        let mut image = Part::raw(vec![0, 1, 2]);
        image.filename = Some("photo.png".into());
        image.media_type = Some("image/png".into());
        let incoming = read_parts(&[Part::text("as slack"), file, image]);
        assert_eq!(incoming.text, "as slack\n\n{\"a\": 1}");
        assert_eq!(
            incoming.notes,
            vec!["I can't read photo.png (image/png); paste its text instead."]
        );
    }

    #[test]
    fn line_endings_are_normalised() {
        assert_eq!(read_parts(&[Part::text("a\r\nb\rc")]).text, "a\nb\nc");
    }

    #[test]
    fn oversized_file_is_refused() {
        let big = vec![b'a'; MAX_FILE_BYTES + 1];
        assert!(decode_file("a.txt", None, Some(&big)).is_err_and(|e| e.contains("over 5 MB")));
    }
}
