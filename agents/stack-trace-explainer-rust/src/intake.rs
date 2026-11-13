//! Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and no turn
//! pauses: the earlier trace, code and answer come from the platform's chat history.
//!
//! ```text
//! greeting / small talk                                    -> canned intro (no call)
//! answering our "paste the error" reply                    -> the message is the trace (or "cancel")
//! a trace in the history -+- a new trace pasted            -> explain it (turn 1 again)
//!                         +- code pasted, no trace         -> attach to that trace, explain again (one call)
//!                         +- "as json" and nothing else    -> explain that trace again, as JSON
//!                         +- about that error              -> follow-up question (one call)
//!                         +- anything else                 -> relatedness check, then decline or follow up
//! error text or a trace                                    -> explain
//! an earlier explanation, but its trace was trimmed        -> say so, ask for the trace again (no call)
//! no error text                                            -> scope check (decline, or ask "Paste the error")
//! ```

use std::sync::LazyLock;

use regex::Regex;

use crate::history::{self, ASSISTANT, Turn, USER};
use crate::inputs::{Incoming, from_envelope, strip_markers};
use crate::paste::{self, CodeBlock, Paste};
use crate::prompts;
use crate::relevance;
use crate::smalltalk;
use crate::trace_parse;
use crate::util::{collapse_ws, re, take_chars};

pub const JSON_ONLY_MAX_WORDS: usize = 6;
pub const REQUEST_MAX_CHARS: usize = 500;
const MARKER_PREFIX_CHARS: usize = 40;

static CANCEL: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\W*$"));
static TRUST_LIBRARY: LazyLock<Regex> = LazyLock::new(|| {
    re(r"(?i)\bmy (?:own )?(?:library|package|lib|module)\b|\bbug is in (?:the|my) library\b")
});

/// The newest trace the user pasted that is still in the history, the code sent with or after it, and
/// the newest reply after it (the earlier answer a follow-up builds on; "" when trimmed).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Earlier {
    pub paste: Paste,
    pub blocks: Vec<CodeBlock>,
    pub trust_libraries: bool,
    pub last_reply: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Route {
    Reply(String),
    Explain {
        paste: Paste,
        trust_libraries: bool,
    },
    Attach {
        blocks: Vec<CodeBlock>,
        earlier: Earlier,
    },
    Json {
        earlier: Earlier,
    },
    Followup {
        earlier: Earlier,
        /// Uses error words or names something from the trace: no relatedness check needed.
        clearly_related: bool,
    },
    Scope,
}

pub fn plan_turn(incoming: &Incoming) -> Route {
    if let Some(canned) = smalltalk::reply_to(&incoming.text) {
        return Route::Reply(canned);
    }
    let turns = &incoming.history;
    if history::last_assistant(turns)
        .starts_with(take_chars(prompts::ASK_TRACE_TEXT, MARKER_PREFIX_CHARS))
    {
        return answer_to_ask(&incoming.text, turns);
    }
    let text =
        strip_markers(&from_envelope(&incoming.text).unwrap_or_else(|| incoming.text.clone()));
    let pasted = paste::split(&text);
    let trust = trusts_libraries(if pasted.words.is_empty() {
        &text
    } else {
        &pasted.words
    });
    if let Some(earlier) = earlier_trace(turns) {
        return followup_route(&text, pasted, incoming.wants_json, trust, earlier);
    }
    if has_error(&pasted) {
        return Route::Explain {
            paste: pasted,
            trust_libraries: trust,
        };
    }
    let note = take_chars(prompts::READ_ONLY_NOTE, MARKER_PREFIX_CHARS);
    if turns
        .iter()
        .any(|t| t.role == ASSISTANT && t.text.contains(note))
    {
        return Route::Reply(prompts::TRACE_TRIMMED.to_string());
    }
    Route::Scope
}

/// The message after our "paste the error" reply: any text is taken as the trace; "cancel" stops.
fn answer_to_ask(answer: &str, turns: &[Turn]) -> Route {
    let text = answer.trim();
    if text.is_empty() || CANCEL.is_match(&collapse_ws(&text.to_lowercase())) {
        return Route::Reply(prompts::CANCELLED_REPLY.to_string());
    }
    let request = take_chars(
        history::user_before_last_assistant(turns),
        REQUEST_MAX_CHARS,
    );
    let pasted = resume_paste(request, text);
    let trust = trusts_libraries(&pasted.words);
    Route::Explain {
        paste: pasted,
        trust_libraries: trust,
    }
}

fn followup_route(
    text: &str,
    pasted: Paste,
    wants_json: bool,
    trust: bool,
    earlier: Earlier,
) -> Route {
    if has_new_trace(&pasted) {
        return Route::Explain {
            paste: pasted,
            trust_libraries: trust,
        };
    }
    if !pasted.blocks.is_empty() {
        return Route::Attach {
            blocks: pasted.blocks,
            earlier,
        };
    }
    if let Some(loose) = paste::loose_block(text) {
        return Route::Attach {
            blocks: vec![loose],
            earlier,
        };
    }
    if wants_json && text.split_whitespace().count() <= JSON_ONLY_MAX_WORDS {
        return Route::Json { earlier };
    }
    let trace = earlier
        .paste
        .traces
        .get(earlier.paste.primary)
        .map_or("", String::as_str);
    let clearly_related = relevance::clearly_about(text, trace, &earlier.last_reply);
    Route::Followup {
        earlier,
        clearly_related,
    }
}

fn has_error(pasted: &Paste) -> bool {
    let mut joined = vec![pasted.words.as_str()];
    joined.extend(pasted.traces.iter().map(String::as_str));
    has_new_trace(pasted) || trace_parse::has_error_text(&joined.join("\n"))
}

/// Newest first: the user turn with a trace, and the code pasted with or after it.
pub fn earlier_trace(turns: &[Turn]) -> Option<Earlier> {
    let mut later_blocks: Vec<CodeBlock> = Vec::new();
    for (index, turn) in turns.iter().enumerate().rev() {
        if turn.role != USER {
            continue;
        }
        let text = strip_markers(&from_envelope(&turn.text).unwrap_or_else(|| turn.text.clone()));
        let pasted = paste::split(&text);
        if has_error(&pasted) {
            let last_reply = turns[index + 1..]
                .iter()
                .rfind(|t| t.role == ASSISTANT)
                .map_or(String::new(), |t| t.text.clone());
            let mut blocks = pasted.blocks.clone();
            blocks.extend(later_blocks.into_iter().rev());
            let trust = trusts_libraries(if pasted.words.is_empty() {
                &text
            } else {
                &pasted.words
            });
            return Some(Earlier {
                paste: pasted,
                blocks,
                trust_libraries: trust,
                last_reply,
            });
        }
        let found: Vec<CodeBlock> = if pasted.blocks.is_empty() {
            paste::loose_block(&text).into_iter().collect()
        } else {
            pasted.blocks
        };
        later_blocks.extend(found.into_iter().rev());
    }
    None
}

/// A real trace (frames parsed) or a bare error line with no question mark (`ECONNREFUSED 127.0.0.1:5432`).
pub fn has_new_trace(pasted: &Paste) -> bool {
    if pasted
        .traces
        .iter()
        .any(|t| !trace_parse::parse(t, false).frameless)
    {
        return true;
    }
    let joined = pasted.traces.join("\n");
    !joined.contains('?') && trace_parse::has_typed_error(&joined)
}

/// The paste typed after the "Paste the error" reply, taken as the trace even if it holds no error word.
pub fn resume_paste(request: &str, answer: &str) -> Paste {
    let pasted = paste::split(&strip_markers(answer));
    let words = [request, pasted.words.as_str()]
        .iter()
        .filter(|w| !w.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_string();
    let has_traces = !pasted.traces.is_empty();
    Paste {
        words,
        traces: if has_traces {
            pasted.traces
        } else {
            vec![answer.trim().to_string()]
        },
        blocks: pasted.blocks,
        primary: if has_traces { pasted.primary } else { 0 },
        skipped_traces: pasted.skipped_traces,
        notes: pasted.notes,
    }
}

pub fn trusts_libraries(words: &str) -> bool {
    TRUST_LIBRARY.is_match(words)
}
