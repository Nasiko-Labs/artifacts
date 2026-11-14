//! translator-rust: translates pasted text into about 40 languages at a chosen register. Links, code,
//! numbers and placeholders are masked in code and restored after; every chunk is checked (tokens, digits,
//! structure) and repaired once.
//!
//! Stateless: nothing is kept between messages; follow-ups are rebuilt from the platform's chat history.

pub mod card;
pub mod envelope;
pub mod followup;
pub mod history;
pub mod inputs;
pub mod intake;
pub mod languages;
pub mod llm;
pub mod mask;
pub mod natural;
pub mod prompts;
pub mod recall;
pub mod register;
pub mod render;
pub mod request;
pub mod segments;
pub mod server;
pub mod smalltalk;
pub mod telemetry;
pub mod translate;
pub mod turns;
pub mod util;
pub mod verify;
