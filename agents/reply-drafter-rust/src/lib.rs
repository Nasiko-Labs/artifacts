//! reply-drafter-rust: drafts the user's reply to a message they received, in their own voice. The style is
//! measured in code from 1-3 of their past replies, the model writes one JSON draft, and code checks it
//! (grounding, tone, length, the shorter alternative), repairs once and renders it.
//!
//! Stateless: nothing is kept between messages; revisions are rebuilt from the platform's chat history.

pub mod card;
pub mod checks;
pub mod draft;
pub mod drafting;
pub mod envelope;
pub mod grounding;
pub mod history;
pub mod incoming;
pub mod inputs;
pub mod llm;
pub mod plan;
pub mod prompts;
pub mod recall;
pub mod register;
pub mod render;
pub mod server;
pub mod smalltalk;
pub mod style;
pub mod telemetry;
pub mod textstats;
pub mod tone_check;
pub mod turns;
pub mod util;
