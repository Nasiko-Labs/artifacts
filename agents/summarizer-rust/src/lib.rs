//! summarizer-rust: summarizes pasted text into a TL;DR, decisions and action items. Every item is backed
//! by a verbatim quote from the text, checked in code; anything that can't be found is left out.
//!
//! Stateless: nothing is kept between messages; follow-ups are rebuilt from the platform's chat history.

pub mod card;
pub mod checks;
pub mod chunking;
pub mod followup;
pub mod grounding;
pub mod history;
pub mod inputs;
pub mod intake;
pub mod items;
pub mod llm;
pub mod mapreduce;
pub mod prompts;
pub mod recall;
pub mod render;
pub mod server;
pub mod smalltalk;
pub mod summarize;
pub mod telemetry;
pub mod turns;
pub mod util;
