//! meeting-notes-processor-rust: turns a pasted meeting transcript or raw notes into attendees, topic
//! notes, decisions, and action items and follow-ups with owners and due dates. Every decision and action
//! is backed by a verbatim quote that code finds in its turn.
//!
//! Stateless: nothing is kept between messages; follow-ups are rebuilt from the platform's chat history.

pub mod attendees;
pub mod card;
pub mod checks;
pub mod chunking;
pub mod envelope;
pub mod followup;
pub mod grounding;
pub mod history;
pub mod inputs;
pub mod intake;
pub mod items;
pub mod llm;
pub mod mapreduce;
pub mod owners;
pub mod process;
pub mod prompts;
pub mod recall;
pub mod render;
pub mod server;
pub mod smalltalk;
pub mod telemetry;
pub mod timecode;
pub mod transcript;
pub mod turns;
pub mod util;
