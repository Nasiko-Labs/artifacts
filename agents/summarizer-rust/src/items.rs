//! The summary's records.

pub const NOT_STATED: &str = "Not stated";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Tldr,
    Decision,
    Action,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Tldr => "tldr",
            Kind::Decision => "decision",
            Kind::Action => "action",
        }
    }

    /// The first letter, used in map-reduce ids (`c3.a2`).
    pub fn letter(self) -> char {
        match self {
            Kind::Tldr => 't',
            Kind::Decision => 'd',
            Kind::Action => 'a',
        }
    }
}

/// One checked line: `snippet` is a verbatim quote found in the source; `owner`/`due` are actions only.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub kind: Kind,
    pub text: String,
    pub snippet: String,
    pub owner: String,
    pub due: String,
    pub item_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Summary {
    pub tldr: Vec<Item>,
    pub decisions: Vec<Item>,
    pub actions: Vec<Item>,
    /// Items the code could not back with the source.
    pub dropped: usize,
    /// Items over the section caps.
    pub capped: usize,
    pub notes: Vec<String>,
}

impl Summary {
    pub fn is_empty(&self) -> bool {
        self.tldr.is_empty()
    }

    pub fn all_items(&self) -> Vec<&Item> {
        self.tldr
            .iter()
            .chain(&self.decisions)
            .chain(&self.actions)
            .collect()
    }
}
