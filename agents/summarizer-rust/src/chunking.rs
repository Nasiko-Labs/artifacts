//! Pure: split a long source into overlapping chunks on paragraph / speaker-turn / sentence boundaries.
//! Sizes and offsets are in chars.

pub const SINGLE_PASS_MAX_CHARS: usize = 24_000;
pub const CHUNK_CHARS: usize = 12_000;
pub const OVERLAP_CHARS: usize = 400;
pub const MAX_CHUNKS: usize = 10;
const BOUNDARIES: &[&str] = &["\n\n", "\n", ". ", " "];

/// CHUNK_CHARS, grown just enough that a source at the size cap still fits in MAX_CHUNKS chunks.
pub fn chunk_size_for(length: usize) -> usize {
    let needed = length.saturating_sub(OVERLAP_CHARS).div_ceil(MAX_CHUNKS) + OVERLAP_CHARS;
    CHUNK_CHARS.max(needed + CHUNK_CHARS / 20)
}

/// Chunks of at most `size` chars; each after the first starts `overlap` chars back, at a line start when
/// one is in reach, so a speaker label is not cut off. Without an explicit size, never more than
/// MAX_CHUNKS chunks.
pub fn split(source: &str, size: Option<usize>) -> Vec<String> {
    let chars: Vec<char> = source.chars().collect();
    if let Some(size) = size {
        return split_chars(&chars, size, OVERLAP_CHARS);
    }
    let mut size = chunk_size_for(chars.len());
    let mut chunks = split_chars(&chars, size, OVERLAP_CHARS);
    while chunks.len() > MAX_CHUNKS {
        // boundary cuts ran short: widen until the call budget holds
        size += size / 10;
        chunks = split_chars(&chars, size, OVERLAP_CHARS);
    }
    chunks
}

fn split_chars(source: &[char], size: usize, overlap: usize) -> Vec<String> {
    let size = size.max(1);
    if source.len() <= size {
        return vec![source.iter().collect()];
    }
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < source.len() {
        let end = cut(source, start, size);
        chunks.push(source[start..end].iter().collect());
        if end >= source.len() {
            break;
        }
        start = next_start(source, start, end, overlap);
    }
    chunks
}

/// The last occurrence of `pattern` starting in `[lo, hi)` and ending by `hi`.
fn rfind(source: &[char], pattern: &str, lo: usize, hi: usize) -> Option<usize> {
    let pat: Vec<char> = pattern.chars().collect();
    if hi < pat.len() {
        return None;
    }
    (lo..=hi - pat.len())
        .rev()
        .find(|&i| source[i..i + pat.len()] == pat[..])
}

fn cut(source: &[char], start: usize, size: usize) -> usize {
    let end = start + size;
    if end >= source.len() {
        return source.len();
    }
    let floor = start + size / 2;
    for boundary in BOUNDARIES {
        if let Some(at) = rfind(source, boundary, floor, end) {
            return at + boundary.chars().count();
        }
    }
    end
}

fn next_start(source: &[char], start: usize, end: usize, overlap: usize) -> usize {
    let back = end.saturating_sub(overlap).max(start + 1);
    match source[back..end].iter().position(|&c| c == '\n') {
        Some(i) if back + i + 1 < end => back + i + 1,
        _ => back,
    }
}
