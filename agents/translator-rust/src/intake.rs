//! Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing
//! ever pauses: every route ends the task `completed`. The last job comes only from the platform's
//! history block (`recall::replay`).
//!
//! ```text
//! greeting / small talk                         -> canned intro (no call)
//! instruction + text, or an envelope            -> translate (over the cap: the first part, said so)
//! a natural request ("how do you say X in German") -> translate (`natural::parse`, no call)
//! short follow-up on the last job               -> original (no call) or re-translate / json (one run)
//!   ... whose text was trimmed from the history -> translate the last translation into the new language
//!                                                  (said so), else ask for a re-paste
//! an instruction with no text                   -> reply: paste the text (completed)
//! text with no target language                  -> reply: which language? (completed; the answer works
//!                                                  from the history)
//! anything else                                 -> one scope call: the text (and language) to translate,
//!                                                  or declined
//! ```

use serde_json::{Map, Value};

use crate::followup::{self, FollowUp};
use crate::inputs::Incoming;
use crate::languages;
use crate::natural;
use crate::prompts;
use crate::recall;
use crate::render;
use crate::request::{self, Job, MAX_SOURCE_CHARS};
use crate::smalltalk;
use crate::util::{char_len, take_chars, thousands};

pub const TOO_BIG_ENVELOPE: &str =
    "That agent output is over my size limit (about 60,000 characters), so I can't translate it.";
const UNKNOWN_NAME_MAX_CHARS: usize = 40;

#[derive(Debug, Clone, PartialEq)]
pub enum Plan {
    Reply(String),
    Translate { job: Job, notes: Vec<String> },
    Scope(Job),
}

fn translate(job: Job) -> Plan {
    Plan::Translate {
        job,
        notes: Vec::new(),
    }
}

pub fn plan_turn(incoming: &Incoming) -> Plan {
    if incoming.data.is_none()
        && let Some(canned) = smalltalk::reply_to(&incoming.text)
    {
        return Plan::Reply(canned);
    }
    let stored = recall::replay(&incoming.history);
    let text = prompts::strip_markers(&incoming.text);
    let (instruction, body) = request::split(&text);
    let envelope = envelope_of(incoming.data.as_ref()).or_else(|| request::envelope_from(&body));
    let base = recall::carry(stored.as_ref(), incoming.as_json);
    if envelope.is_some() || (!instruction.is_empty() && !body.is_empty()) {
        let job = Job {
            source: if envelope.is_some() {
                String::new()
            } else {
                body
            },
            envelope,
            ..base
        };
        return next_step(
            request::apply_instruction(job, &instruction),
            incoming,
            false,
        );
    }
    if let Some(follow) = followup::parse(&text, stored.as_ref()) {
        return follow_up(follow, stored, incoming);
    }
    if let Some((instruction, source)) = natural::parse(&text) {
        // "how do you say thank you in German", "translate I am hungry into spanish"
        let job = Job { source, ..base };
        return next_step(
            request::apply_instruction(job, &instruction),
            incoming,
            false,
        );
    }
    if !instruction.is_empty() {
        // a cue with no text
        return next_step(
            request::apply_instruction(base, &instruction),
            incoming,
            false,
        );
    }
    Plan::Scope(Job {
        source: body,
        ..base
    })
}

/// The scope call's extracted text and target applied to `job`: the checked text, and the named target (a
/// name that is not a known language clears the target and is reported as unknown).
pub fn from_extraction(job: Job, message: &str, text: &str, target: &str) -> Job {
    let (source, found, unknown) = natural::checked_extraction(text, target, message);
    match found {
        Some(found) => Job {
            source,
            target: found.language.code.to_string(),
            target_note: found.note.to_string(),
            unknown: String::new(),
            ..job
        },
        None if !unknown.is_empty() => Job {
            source,
            target: String::new(),
            target_note: String::new(),
            unknown,
            ..job
        },
        None => Job { source, ..job },
    }
}

fn follow_up(follow: FollowUp, stored: Option<Job>, incoming: &Incoming) -> Plan {
    let with_text = stored.filter(Job::has_text);
    match follow {
        FollowUp::Original => Plan::Reply(match &with_text {
            Some(job) => render::original(&job.source, job.envelope.as_ref()),
            None => prompts::NOTHING_STORED.to_string(),
        }),
        FollowUp::Json => match with_text {
            Some(job) => next_step(
                Job {
                    as_json: true,
                    ..job
                },
                incoming,
                false,
            ),
            None => Plan::Reply(prompts::NOTHING_STORED.to_string()),
        },
        FollowUp::Retranslate(job) => {
            let as_json = incoming.as_json || job.as_json;
            next_step(Job { as_json, ..*job }, incoming, true)
        }
    }
}

pub fn next_step(job: Job, incoming: &Incoming, is_follow_up: bool) -> Plan {
    if !job.has_text() {
        return without_text(job, incoming, is_follow_up);
    }
    let envelope_size = job
        .envelope
        .as_ref()
        .map_or(0, |e| char_len(&Value::Object(e.clone()).to_string()));
    if envelope_size > MAX_SOURCE_CHARS {
        return Plan::Reply(TOO_BIG_ENVELOPE.to_string());
    }
    if job.target.is_empty() {
        let lead = if job.unknown.is_empty() {
            String::new()
        } else {
            prompts::unsupported(
                take_chars(&job.unknown, UNKNOWN_NAME_MAX_CHARS),
                &languages::supported_names().join(", "),
            )
        };
        return Plan::Reply(prompts::need_language(&lead));
    }
    let total = char_len(&job.source);
    if total > MAX_SOURCE_CHARS {
        let kept = request::cut(&job.source, MAX_SOURCE_CHARS);
        let used = char_len(&kept);
        let note = prompts::too_big_note(
            &thousands(total),
            &thousands(MAX_SOURCE_CHARS),
            &thousands(used),
            &thousands(total - used),
        );
        return Plan::Translate {
            job: Job {
                source: kept,
                ..job
            },
            notes: vec![note],
        };
    }
    translate(job)
}

/// No text in this message or the history: translate the last translation into a NEW language when one
/// is still visible (said so), else ask for the text.
fn without_text(job: Job, incoming: &Incoming, is_follow_up: bool) -> Plan {
    if let Some((text, code)) = recall::last_translation(&incoming.history)
        && !job.target.is_empty()
        && !code.eq_ignore_ascii_case(&job.target)
    {
        let note = prompts::from_translation_note(&languages::name_of(&code));
        let job = Job {
            source: text,
            source_hint: languages::name_of(&code),
            ..job
        };
        return Plan::Translate {
            job,
            notes: vec![note],
        };
    }
    if is_follow_up && !incoming.history.is_empty() {
        return Plan::Reply(prompts::NOTHING_STORED.to_string());
    }
    let into = if job.target.is_empty() {
        String::new()
    } else {
        format!(" into {}", languages::name_of(&job.target))
    };
    Plan::Reply(prompts::need_text(&into))
}

fn envelope_of(data: Option<&Map<String, Value>>) -> Option<Map<String, Value>> {
    let data = data.filter(|d| !d.is_empty())?;
    request::envelope_from(&Value::Object(data.clone()).to_string())
}
