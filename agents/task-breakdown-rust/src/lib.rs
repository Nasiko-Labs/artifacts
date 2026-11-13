//! task-breakdown-rust: turns a goal, spec or brief into a structured work plan (phases, sized tasks,
//! dependencies, acceptance criteria); code computes the waves, the critical path, team lanes and the fit
//! to a timebox.
//!
//! Stateless: nothing is kept between messages; follow-ups read the plan back from the platform's chat
//! history (or from a plan the user pastes again).

pub mod answers;
pub mod card;
pub mod checks;
pub mod envelope;
pub mod envelope_in;
pub mod fence;
pub mod finalize;
pub mod fit;
pub mod fixes;
pub mod grounding;
pub mod history;
pub mod inputs;
pub mod intake;
pub mod llm;
pub mod mapping;
pub mod metrics;
pub mod models;
pub mod plan_text;
pub mod planfile;
pub mod planning;
pub mod prompts;
pub mod queries;
pub mod recall;
pub mod render;
pub mod revision;
pub mod server;
pub mod smalltalk;
pub mod taskgraph;
pub mod telemetry;
pub mod turns;
pub mod util;
