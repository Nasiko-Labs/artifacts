//! Pure: the transcript as model input lines, and its split into chunks on turn boundaries.
//!
//! A transcript up to SINGLE_PASS_MAX_CHARS goes in one call. Above it, chunks of about CHUNK_CHARS,
//! each after the first starting with the last OVERLAP_TURNS turns of the one before, never more than
//! MAX_CHUNKS (the chunk size grows to fit). A single turn longer than a chunk is a chunk on its own.
//! Sizes are in chars.

use crate::items::Turn;
use crate::util::char_len;

pub const SINGLE_PASS_MAX_CHARS: usize = 24_000;
pub const CHUNK_CHARS: usize = 12_000;
pub const OVERLAP_TURNS: usize = 2;
pub const MAX_CHUNKS: usize = 10;

pub fn line(turn: &Turn) -> String {
    let when = if turn.time.is_empty() {
        String::new()
    } else {
        format!(" [{}]", turn.time)
    };
    format!("{}{when} {}: {}", turn.id, turn.speaker, turn.text)
}

pub fn as_text(turns: &[Turn]) -> String {
    turns.iter().map(line).collect::<Vec<_>>().join("\n")
}

pub fn chunk_size_for(length: usize) -> usize {
    CHUNK_CHARS.max(length.div_ceil(MAX_CHUNKS) + CHUNK_CHARS / 10)
}

/// The turns in one chunk (a single pass) or several overlapping ones.
pub fn split(turns: &[Turn], size: Option<usize>) -> Vec<Vec<Turn>> {
    let total = char_len(&as_text(turns));
    if let Some(size) = size {
        return split_by(turns, size);
    }
    if total <= SINGLE_PASS_MAX_CHARS {
        return vec![turns.to_vec()];
    }
    let mut size = chunk_size_for(total);
    let mut chunks = split_by(turns, size);
    while chunks.len() > MAX_CHUNKS {
        size += size / 10;
        chunks = split_by(turns, size);
    }
    chunks
}

fn split_by(turns: &[Turn], size: usize) -> Vec<Vec<Turn>> {
    let lengths: Vec<usize> = turns.iter().map(|t| char_len(&line(t)) + 1).collect();
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < turns.len() {
        let (mut end, mut used) = (start, 0);
        while end < turns.len() && (end == start || used + lengths[end] <= size) {
            used += lengths[end];
            end += 1;
        }
        chunks.push(turns[start..end].to_vec());
        if end >= turns.len() {
            break;
        }
        start = end.saturating_sub(OVERLAP_TURNS).max(start + 1);
    }
    chunks
}
