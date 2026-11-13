//! Pure: character-indexed access to a text. Every offset, size and cap in this agent counts characters,
//! like the original; regexes run on the UTF-8 text and their byte offsets are mapped back here.

pub struct Text<'a> {
    pub s: &'a str,
    /// Byte offset of each character, plus `s.len()` at the end.
    bytes: Vec<usize>,
}

impl<'a> Text<'a> {
    pub fn new(s: &'a str) -> Self {
        let mut bytes: Vec<usize> = s.char_indices().map(|(b, _)| b).collect();
        bytes.push(s.len());
        Self { s, bytes }
    }

    /// Length in characters.
    pub fn len(&self) -> usize {
        self.bytes.len() - 1
    }

    pub fn byte(&self, char_index: usize) -> usize {
        self.bytes[char_index.min(self.len())]
    }

    /// The character index of a byte offset that lies on a character boundary.
    pub fn char_at(&self, byte: usize) -> usize {
        self.bytes.binary_search(&byte).unwrap_or_else(|i| i)
    }

    /// Characters `a..b`.
    pub fn slice(&self, a: usize, b: usize) -> &'a str {
        &self.s[self.byte(a)..self.byte(b.max(a))]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn character_offsets() {
        let t = Text::new("aé€b");
        assert_eq!(t.len(), 4);
        assert_eq!(t.slice(1, 3), "é€");
        assert_eq!(t.char_at(t.byte(3)), 3);
    }
}
