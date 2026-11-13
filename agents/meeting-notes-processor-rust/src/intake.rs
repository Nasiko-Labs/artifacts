//! Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing
//! ever pauses: every route ends the task `completed`. Earlier turns come only from the platform's history
//! block (`recall.rs`).
//!
//! ```text
//! greeting / small talk                            -> canned intro (no call)
//! >= 80 words of transcript -+- over the size cap   -> process the first ~20k words, say what was left out
//!                            +- otherwise           -> process
//! "I'm Raj"                                        -> "mine" from the last notes reply, else process the
//!                                                     transcript in history, else "noted, paste it"
//! view command ("only decisions", "as a table")    -> cut from the last notes reply (no call), else process
//!                                                     the transcript in history, else ask for a re-paste
//! "process this" with no text                      -> the transcript in history, else ask to paste it
//! a question                                       -> one quote-checked call over the transcript in
//!                                                     history, else over the last notes reply
//! otherwise                                        -> "too short" (no call)
//! ```
//!
//! A transcript pasted in the current message always wins over anything in the history.

use std::sync::LazyLock;

use crate::followup;
use crate::inputs::{
    Incoming, MAX_SOURCE_CHARS, MIN_SOURCE_WORDS, cut, my_name_in, name_only, word_count,
};
use crate::prompts;
use crate::recall;
use crate::render::View;
use crate::smalltalk;
use crate::util::{char_len, take_chars, thousands};

pub const SHORT_REQUEST_MAX_WORDS: usize = 12;
const INSTRUCTION_WITH_FILES_MAX_CHARS: usize = 300;
pub const ASK_NAME: &str =
    "Tell me who you are first (for example \"I'm Raj\"), then ask for only mine.";
pub static NEED_PASTE: LazyLock<String> = LazyLock::new(|| {
    format!(
        "Paste the meeting transcript or notes (at least about {MIN_SOURCE_WORDS} words) in your message \
         and I'll process it."
    )
});
pub const REPASTE: &str = concat!(
    "That meeting is no longer in the part of our conversation I can see (long earlier inputs drop out), ",
    "so paste the transcript again and I'll do that."
);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Material {
    Transcript,
    Notes,
    Nothing,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Route {
    Reply(String),
    Process,
    Ask(Material),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub route: Route,
    /// Process: the transcript; ask: the material to answer from.
    pub source: String,
    pub instruction: String,
    pub my_name: String,
    pub view: View,
    pub wants_json: bool,
    pub notes: Vec<String>,
}

impl Plan {
    fn new(route: Route, notes: &[String]) -> Self {
        Self {
            route,
            source: String::new(),
            instruction: String::new(),
            my_name: String::new(),
            view: View::Default,
            wants_json: false,
            notes: notes.to_vec(),
        }
    }

    fn reply(text: &str, notes: &[String]) -> Self {
        Self::new(Route::Reply(text.to_string()), notes)
    }

    fn process(source: String, instruction: String, my_name: String, notes: &[String]) -> Self {
        let (source, mut all) = within_cap(source);
        all.extend(notes.iter().cloned());
        Self {
            source,
            instruction,
            my_name,
            ..Self::new(Route::Process, &all)
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
    let (instruction, source) = source_of(incoming);
    if word_count(&source) >= MIN_SOURCE_WORDS {
        let mut my_name = my_name_in(&instruction);
        if my_name.is_empty() {
            my_name = recall::my_name(&incoming.history);
        }
        let plan = Plan::process(source, instruction, my_name, &incoming.notes);
        return Plan {
            wants_json: incoming.wants_json,
            ..plan
        };
    }
    let name = if incoming.files.is_empty() {
        name_only(&incoming.text)
    } else {
        String::new()
    };
    if !name.is_empty() {
        return set_name(incoming, &name);
    }
    if let Some(view) = followup::parse_command(&incoming.text) {
        return view_plan(incoming, view, incoming.wants_json || view == View::Json);
    }
    let short = word_count(&incoming.text) <= SHORT_REQUEST_MAX_WORDS;
    if followup::is_process_request(&incoming.text) && short {
        return from_history(incoming, View::Default, incoming.wants_json, &NEED_PASTE);
    }
    if followup::is_question_like(&incoming.text) {
        return ask(incoming);
    }
    Plan::reply(&smalltalk::TOO_SHORT_REPLY, &incoming.notes)
}

/// The source cut to the size cap (from the start, at a line break), and a note saying what was left out.
pub fn within_cap(source: String) -> (String, Vec<String>) {
    if char_len(&source) <= MAX_SOURCE_CHARS {
        return (source, Vec::new());
    }
    let kept = cut(&source, MAX_SOURCE_CHARS);
    let (total, used) = (word_count(&source), word_count(&kept));
    let note = format!(
        "That was about {} words, over my limit of about 20k, so I processed only the first {} words; the \
         last {} words were left out. Paste the rest in a new message to process it separately.",
        thousands(total),
        thousands(used),
        thousands(total.saturating_sub(used))
    );
    (kept, vec![note])
}

fn name_of(incoming: &Incoming) -> String {
    let said = my_name_in(&incoming.text);
    if said.is_empty() {
        recall::my_name(&incoming.history)
    } else {
        said
    }
}

fn set_name(incoming: &Incoming, name: &str) -> Plan {
    if let Some(shown) = recall::view_from_history(&incoming.history, View::Mine, name) {
        return Plan::reply(
            &format!("Noted: you're {name}.\n\n{shown}"),
            &incoming.notes,
        );
    }
    let (instruction, source) = recall::transcript(&incoming.history);
    if !source.is_empty() {
        return Plan::process(source, instruction, name.to_string(), &incoming.notes);
    }
    Plan::reply(
        &format!("Noted: you're {name}. Paste a meeting transcript and I'll mark your items."),
        &incoming.notes,
    )
}

fn view_plan(incoming: &Incoming, view: View, wants_json: bool) -> Plan {
    let name = name_of(incoming);
    if view == View::Mine && name.is_empty() {
        return Plan::reply(ASK_NAME, &incoming.notes);
    }
    let shown = if wants_json {
        None
    } else {
        recall::view_from_history(&incoming.history, view, &name)
    };
    if let Some(shown) = shown {
        return Plan::reply(&shown, &incoming.notes);
    }
    from_history(incoming, view, wants_json, REPASTE)
}

/// Process the transcript still in the history and show `view`, or reply `otherwise`.
fn from_history(incoming: &Incoming, view: View, wants_json: bool, otherwise: &str) -> Plan {
    let (instruction, source) = recall::transcript(&incoming.history);
    if source.is_empty() {
        return Plan::reply(otherwise, &incoming.notes);
    }
    let plan = Plan::process(source, instruction, name_of(incoming), &incoming.notes);
    Plan {
        view,
        wants_json,
        ..plan
    }
}

fn ask(incoming: &Incoming) -> Plan {
    let (_instruction, source) = recall::transcript(&incoming.history);
    if !source.is_empty() {
        return Plan {
            source: within_cap(source).0,
            ..Plan::new(Route::Ask(Material::Transcript), &incoming.notes)
        };
    }
    let reply = recall::notes_reply(&incoming.history);
    if !reply.is_empty() {
        return Plan {
            source: reply.to_string(),
            ..Plan::new(Route::Ask(Material::Notes), &incoming.notes)
        };
    }
    Plan::new(Route::Ask(Material::Nothing), &incoming.notes)
}

/// (instruction, transcript) with fence markers stripped from both.
pub fn source_of(incoming: &Incoming) -> (String, String) {
    if incoming.files.is_empty() {
        return recall::source_of_text(&incoming.text);
    }
    let files: Vec<&str> = incoming
        .files
        .iter()
        .map(|(_, text)| text.trim())
        .filter(|t| !t.is_empty())
        .collect();
    let instruction = prompts::strip_markers(incoming.text.trim());
    (
        take_chars(&instruction, INSTRUCTION_WITH_FILES_MAX_CHARS).to_string(),
        prompts::strip_markers(&files.join("\n\n")),
    )
}
