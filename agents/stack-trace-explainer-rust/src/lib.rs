//! stack-trace-explainer-rust: explains a pasted error or stack trace. The trace is parsed in code, the
//! line to look at is picked in code, and the model explains the probable cause, a fix and how to
//! verify it; every quote, path and line number it returns is checked against the paste.
//!
//! Stateless: nothing is kept between messages; follow-ups are rebuilt from the platform's chat history.

pub mod answer;
pub mod card;
pub mod checks;
pub mod envelope;
pub mod explain;
pub mod frames;
pub mod hints;
pub mod history;
pub mod inputs;
pub mod intake;
pub mod llm;
pub mod parse_jvm;
pub mod parse_other;
pub mod parse_python;
pub mod paste;
pub mod prompts;
pub mod relevance;
pub mod render;
pub mod restate;
pub mod server;
pub mod smalltalk;
pub mod snippets;
pub mod telemetry;
pub mod trace_model;
pub mod trace_parse;
pub mod turns;
pub mod util;
