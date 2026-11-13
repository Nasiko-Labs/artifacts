//! Card text, shared by the served card (`server.rs`) and `AgentCard.json` (checked by a test).
//!
//! The description carries the distinction from other agents: workflow generation sees only the name
//! and description.

pub const AGENT_NAME: &str = "chunker-merger-rust";
pub const AGENT_VERSION: &str = "1.0.0";
pub const AGENT_DESCRIPTION: &str = "Cuts a long text into numbered, ready-to-paste parts and merges the per-part JSON results back into \
one result with the same schema, entirely in code with no model. This is the Rust build. Split: by speaker turns, headings, \
paragraphs or sentences, into N parts or parts of a given size, never mid-sentence, optionally with \
an instruction wrapped into every part's prompt; it checks that the parts rejoin to your text \
exactly. Merge: paste two or more JSON results from the same agent; lists are concatenated and \
exact duplicates removed, confidence is the lowest, needs_human is true if any part needs it, \
near-duplicates are only reported. Stateless: it keeps nothing between messages, so follow-ups (show \
part 3, split again, merge with this one) work on the text or results still visible in the recent \
conversation and a long earlier paste may need re-pasting; a text sent over several messages is split \
piece by piece. It never asks back. It never rewrites content, calls no other agent and answers no \
other questions.";

pub const SKILL_ID: &str = "chunk-and-merge";
pub const SKILL_NAME: &str = "Split long text and merge results";
pub const SKILL_DESCRIPTION: &str = "Splits a long text into numbered chunk prompts on structural boundaries, losslessly, and merges \
same-schema JSON results from the parts into one, without rewriting any item.";
pub const SKILL_TAGS: [&str; 7] = [
    "split",
    "chunk",
    "merge",
    "long text",
    "transcript",
    "json",
    "map-reduce",
];
pub const SKILL_EXAMPLES: [&str; 3] = [
    "Split this transcript into 4 parts, for each part: extract action items <paste the text>",
    "Merge these results: <json> --- <json>",
    "Show part 3",
];
pub const INPUT_MODES: [&str; 4] = [
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
];
pub const OUTPUT_MODES: [&str; 1] = ["text/plain"];
