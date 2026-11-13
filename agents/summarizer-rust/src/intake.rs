//! Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing
//! ever pauses: every route ends the task `completed`. Earlier turns come only from the platform's
//! history block (`recall.rs`).
//!
//! ```text
//! greeting / small talk                         -> canned intro (no call)
//! >= 60 words of source  -+- over the size cap  -> summarize the first ~20k words, say what was left out
//!                         +- refers back ("this one too") -> summarize this one on its own, and say so
//!                         +- otherwise          -> summarize
//! re-render command ("only the action items")   -> rebuilt from the last summary reply (no call), else
//!                                                  summarize the source in history, else ask for a re-paste
//! "summarize this" with no text                 -> the last summary reply, else the source in history,
//!                                                  else ask to paste it
//! a question                                    -> one quote-checked call over the source in history, else
//!                                                  over the last summary reply
//! otherwise                                     -> "short enough to read as is" (no call)
//! ```

use std::sync::LazyLock;

use regex::Regex;

use crate::chunking::SINGLE_PASS_MAX_CHARS;
use crate::followup;
use crate::inputs::{
    Incoming, MAX_SOURCE_CHARS, MIN_SOURCE_WORDS, assemble_source, split_request, truncate_source,
    word_count,
};
use crate::prompts;
use crate::recall;
use crate::render::View;
use crate::smalltalk;
use crate::util::{char_len, re, thousands};

pub const SHORT_REQUEST_MAX_WORDS: usize = 12;
pub const ON_ITS_OWN: &str = concat!(
    "I summarized this text on its own: I keep nothing between messages, so to cover it together ",
    "with an earlier text, paste both in one message."
);
pub static NEED_PASTE: LazyLock<String> = LazyLock::new(|| {
    format!(
        "Paste the text you want summarized (at least about {MIN_SOURCE_WORDS} words) in your message and \
         I'll summarize it."
    )
});
pub const REPASTE: &str = concat!(
    "That text is no longer in the part of our conversation I can see (long earlier inputs drop out), so ",
    "paste it again and I'll do that."
);
pub const FIRST_PART_ASKED: &str = "That text is long, so I answered from its first part only.";
static REFERS_BACK: LazyLock<Regex> = LazyLock::new(|| {
    re(concat!(
        r"(?i)\b(?:too|also|as well|together|combined?|along with|plus this|add(?:ed)? to|",
        r"with the (?:earlier|previous|last|first|other) (?:one|text|summary|thread|call)|",
        r"and this one)\b"
    ))
});

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Route {
    Reply(String),
    Summarize,
    Ask,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub route: Route,
    /// Summarize: the text; ask: the material to answer from.
    pub source: String,
    pub instruction: String,
    pub view: (View, bool),
    pub notes: Vec<String>,
}

impl Plan {
    fn reply(text: &str, notes: &[String]) -> Self {
        Self {
            route: Route::Reply(text.to_string()),
            source: String::new(),
            instruction: String::new(),
            view: (View::Default, false),
            notes: notes.to_vec(),
        }
    }
}

pub fn plan_turn(incoming: &Incoming) -> Plan {
    let canned = incoming
        .files
        .is_empty()
        .then(|| smalltalk::reply_to(&incoming.text))
        .flatten();
    if let Some(canned) = canned {
        return Plan::reply(&canned, &incoming.notes);
    }
    let (instruction, pasted) = split_request(&incoming.text);
    let source = prompts::strip_markers(&assemble_source(&pasted, &incoming.files));
    if word_count(&source) >= MIN_SOURCE_WORDS {
        return new_source(
            source,
            &prompts::strip_markers(&instruction),
            &incoming.notes,
        );
    }
    if let Some(command) = followup::parse_command(&incoming.text) {
        return view(incoming, command, REPASTE);
    }
    let short = word_count(&incoming.text) <= SHORT_REQUEST_MAX_WORDS;
    if followup::is_summarize_request(&incoming.text) && short {
        return view(incoming, (View::Default, false), &NEED_PASTE);
    }
    if followup::is_question_like(&incoming.text) {
        return ask(incoming);
    }
    Plan::reply(&smalltalk::TOO_SHORT_REPLY, &incoming.notes)
}

/// The source cut to the size cap (at a paragraph or line break), and a note saying what was left out.
pub fn within_cap(source: String) -> (String, Vec<String>) {
    if char_len(&source) <= MAX_SOURCE_CHARS {
        return (source, Vec::new());
    }
    let kept = truncate_source(&source, MAX_SOURCE_CHARS);
    let (total, used) = (word_count(&source), word_count(&kept));
    let note = format!(
        "That was about {} words, over my limit of about 20k, so I summarized only the first {} words; the \
         last {} words were left out. Paste the rest in a new message to summarize it separately.",
        thousands(total),
        thousands(used),
        thousands(total.saturating_sub(used))
    );
    (kept, vec![note])
}

fn new_source(source: String, instruction: &str, notes: &[String]) -> Plan {
    let (kept, mut all) = within_cap(source);
    if !instruction.is_empty() && REFERS_BACK.is_match(instruction) {
        all.push(ON_ITS_OWN.to_string());
    }
    all.extend(notes.iter().cloned());
    Plan {
        route: Route::Summarize,
        source: kept,
        instruction: instruction.to_string(),
        view: (View::Default, false),
        notes: all,
    }
}

/// `view` rebuilt from the last summary reply, else the source in history summarized, else `otherwise`.
fn view(incoming: &Incoming, view: (View, bool), otherwise: &str) -> Plan {
    if let Some(shown) = recall::view_from_history(&incoming.history, view.0, view.1) {
        return Plan::reply(&shown, &incoming.notes);
    }
    let (instruction, source) = recall::source(&incoming.history);
    if source.is_empty() {
        return Plan::reply(otherwise, &incoming.notes);
    }
    let (kept, mut notes) = within_cap(source);
    notes.extend(incoming.notes.iter().cloned());
    Plan {
        route: Route::Summarize,
        source: kept,
        instruction,
        view,
        notes,
    }
}

fn ask(incoming: &Incoming) -> Plan {
    let (_instruction, source) = recall::source(&incoming.history);
    let reply = recall::summary_reply(&incoming.history);
    let long = char_len(&source) > SINGLE_PASS_MAX_CHARS;
    let mut notes = Vec::new();
    let material = if !source.is_empty() && (!long || reply.is_empty()) {
        if long {
            notes.push(FIRST_PART_ASKED.to_string());
        }
        truncate_source(&source, SINGLE_PASS_MAX_CHARS)
    } else {
        reply.to_string()
    };
    notes.extend(incoming.notes.iter().cloned());
    Plan {
        route: Route::Ask,
        source: material,
        instruction: String::new(),
        view: (View::Default, false),
        notes,
    }
}
