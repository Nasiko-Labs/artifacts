//! Pure data: a parsed trace. Frames are always stored innermost first, whatever order the language
//! prints them in, so "the innermost app frame" means the same thing for every parser.
//!
//! A trace is one or more exception blocks (a chain). `root` is the index of the block that caused the
//! others (Python: the first block; Java: the last `Caused by`; C#: the innermost `--->`), `surface`
//! the one the program died with.

use std::sync::LazyLock;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Origin {
    App,
    Library,
    None,
}

impl Origin {
    pub fn as_str(self) -> &'static str {
        match self {
            Origin::App => "app",
            Origin::Library => "library",
            Origin::None => "none",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub file: String,
    pub line: i64,
    pub func: String,
    pub col: Option<i64>,
    /// The source line the runtime printed under the frame (Python), if any.
    pub source: String,
    /// App or library (set by `frames::classify`).
    pub origin: Origin,
    /// Consecutive identical frames collapsed into this one.
    pub repeat: i64,
}

impl Frame {
    pub fn new(file: &str, line: i64, func: &str) -> Self {
        Self {
            file: file.to_string(),
            line,
            func: func.to_string(),
            col: None,
            source: String::new(),
            origin: Origin::App,
            repeat: 1,
        }
    }

    pub fn with_col(mut self, col: i64) -> Self {
        self.col = Some(col);
        self
    }

    /// `file:line (func)`, the one form a location is ever shown in.
    pub fn label(&self) -> String {
        let place = format!("{}:{}", self.file, self.line);
        if self.func.is_empty() {
            place
        } else {
            format!("{place} ({})", self.func)
        }
    }

    pub fn same_key(&self, other: &Frame) -> bool {
        self.file == other.file && self.line == other.line && self.func == other.func
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Block {
    pub exception_type: String,
    pub message: String,
    pub frames: Vec<Frame>,
}

impl Block {
    pub fn new(exception_type: &str, message: &str, frames: Vec<Frame>) -> Self {
        Self {
            exception_type: exception_type.to_string(),
            message: message.to_string(),
            frames,
        }
    }

    pub fn headline(&self) -> String {
        if self.message.is_empty() {
            self.exception_type.clone()
        } else {
            format!("{}: {}", self.exception_type, self.message)
        }
    }
}

static EMPTY_BLOCK: LazyLock<Block> = LazyLock::new(Block::default);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Trace {
    pub language: String,
    pub blocks: Vec<Block>,
    pub raw: String,
    pub root: usize,
    pub surface: usize,
    pub frameless: bool,
    /// Python's "[Previous line repeated N more times]", already added to a frame's repeat.
    pub omitted_repeats: i64,
}

impl Trace {
    pub fn new(language: &str, blocks: Vec<Block>, raw: &str) -> Self {
        Self {
            language: language.to_string(),
            blocks,
            raw: raw.to_string(),
            root: 0,
            surface: 0,
            frameless: false,
            omitted_repeats: 0,
        }
    }

    fn block(&self, index: usize) -> &Block {
        self.blocks
            .get(index)
            .or_else(|| self.blocks.first())
            .unwrap_or(&EMPTY_BLOCK)
    }

    pub fn root_block(&self) -> &Block {
        self.block(self.root)
    }

    pub fn surface_block(&self) -> &Block {
        self.block(self.surface)
    }

    pub fn exception_type(&self) -> &str {
        &self.surface_block().exception_type
    }

    pub fn message(&self) -> &str {
        &self.surface_block().message
    }

    /// Every block's headline, root cause first, when there is more than one block.
    pub fn chain(&self) -> Vec<String> {
        let n = self.blocks.len();
        if n < 2 {
            return Vec::new();
        }
        let mut order: Vec<usize> = std::iter::once(self.root)
            .chain((0..n).filter(|&i| i != self.root))
            .collect();
        if self.surface != self.root {
            order = std::iter::once(self.root)
                .chain(order[1..].iter().copied().filter(|&i| i != self.surface))
                .chain(std::iter::once(self.surface))
                .collect();
        }
        order
            .into_iter()
            .map(|i| self.block(i).headline())
            .collect()
    }

    pub fn all_frames(&self) -> Vec<&Frame> {
        self.blocks.iter().flat_map(|b| b.frames.iter()).collect()
    }
}
