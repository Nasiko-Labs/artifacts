//! Pure: catalog-aware templates. A template is chosen when all its required keys are present; it only
//! sets section order, headings and a layout hint, never content. Keys a template does not name are kept
//! and shown under "Other fields" (see shapes.rs).

use serde_json::{Map, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Hint {
    None,
    Markdown,
    Cards,
}

#[derive(Debug, Clone, Copy)]
pub struct Slot {
    pub key: &'static str,
    pub heading: &'static str,
    pub hint: Hint,
    /// Preferred first columns of a table.
    pub columns: &'static [&'static str],
}

const fn slot(key: &'static str, heading: &'static str) -> Slot {
    Slot {
        key,
        heading,
        hint: Hint::None,
        columns: &[],
    }
}

const fn hinted(key: &'static str, heading: &'static str, hint: Hint) -> Slot {
    Slot {
        key,
        heading,
        hint,
        columns: &[],
    }
}

#[derive(Debug)]
pub struct Template {
    pub name: &'static str,
    pub required: &'static [&'static str],
    pub slots: &'static [Slot],
}

pub static TEMPLATES: [Template; 9] = [
    Template {
        name: "summary",
        required: &["tldr", "decisions", "action_items"],
        slots: &[
            slot("tldr", "TL;DR"),
            slot("decisions", "Decisions"),
            slot("open_questions", "Open questions"),
            slot("action_items", "Action items"),
            slot("reversals", "Reversals"),
        ],
    },
    Template {
        name: "promises",
        required: &["promises", "overdue"],
        slots: &[slot("promises", "Promises"), slot("overdue", "Overdue")],
    },
    Template {
        name: "notes",
        required: &["notes", "decisions", "action_items"],
        slots: &[
            hinted("notes", "Notes", Hint::Markdown),
            slot("decisions", "Decisions"),
            slot("action_items", "Action items"),
        ],
    },
    Template {
        name: "ranked",
        required: &["ranked", "drop_or_delegate"],
        slots: &[
            Slot {
                key: "ranked",
                heading: "Ranked",
                hint: Hint::None,
                columns: &["rank", "task", "score", "reason"],
            },
            slot("drop_or_delegate", "Drop or delegate"),
        ],
    },
    Template {
        name: "pre-mortem",
        required: &["failure_modes"],
        slots: &[
            hinted("failure_modes", "Failure modes", Hint::Cards),
            slot("unstated_assumptions", "Unstated assumptions"),
            slot("questions_for_the_team", "Questions for the team"),
        ],
    },
    Template {
        name: "draft",
        required: &["draft", "alt_shorter", "tone_notes"],
        slots: &[
            slot("draft", "Draft"),
            slot("alt_shorter", "Shorter version"),
            slot("tone_notes", "Tone notes"),
        ],
    },
    Template {
        name: "memo",
        required: &["memo_markdown", "recommendation"],
        slots: &[
            hinted("memo_markdown", "Memo", Hint::Markdown),
            slot("recommendation", "Recommendation"),
            slot("reversal_triggers", "Reversal triggers"),
        ],
    },
    Template {
        name: "translation",
        required: &["translation"],
        slots: &[slot("translation", "Translation"), slot("notes", "Notes")],
    },
    Template {
        name: "text",
        required: &["text", "ai_pattern_density"],
        slots: &[slot("text", "Text"), slot("ai_pattern_density", "Density")],
    },
];

/// The first template whose required keys are all present, else None (the generic renderer).
pub fn find(body: &Map<String, Value>) -> Option<&'static Template> {
    TEMPLATES
        .iter()
        .find(|t| t.required.iter().all(|k| body.contains_key(*k)))
}
