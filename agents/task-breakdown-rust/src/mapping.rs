//! Map step for a long spec: one call per chunk (at most 4, concurrent) lists its requirements; code keeps a
//! requirement only when its verbatim fragment is really in that chunk, then merges and dedupes by text.

use serde_json::Value;

use crate::grounding::{is_verbatim, norm};
use crate::llm::TurnDeps;
use crate::prompts;

pub const MAX_REQUIREMENTS: usize = 80;
pub const MAP_MAX_TOKENS: u32 = 2_500;

fn valid(data: &Value) -> Option<Value> {
    let ok = data.is_object() && data.get("requirements").is_none_or(Value::is_array);
    ok.then(|| data.clone())
}

async fn one(deps: &TurnDeps, chunk: &str, part: usize, total: usize) -> Vec<String> {
    let Ok(Some(data)) = deps
        .ask_json(
            &prompts::map_messages(chunk, part, total),
            valid,
            MAP_MAX_TOKENS,
        )
        .await
    else {
        return Vec::new();
    };
    let items = data
        .get("requirements")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    items
        .iter()
        .filter_map(|item| {
            let text = item
                .get("text")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim();
            let verbatim = item
                .get("verbatim")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim();
            (!text.is_empty() && is_verbatim(verbatim, chunk)).then(|| text.to_string())
        })
        .collect()
}

/// (merged requirements, how many chunks answered).
pub async fn requirements(deps: &TurnDeps, chunks: &[String]) -> (Vec<String>, usize) {
    let total = chunks.len();
    let results = futures::future::join_all(
        chunks
            .iter()
            .enumerate()
            .map(|(i, c)| one(deps, c, i + 1, total)),
    )
    .await;
    // Like a dict keyed by the normalised text: the first occurrence keeps its place, the last one its wording.
    let mut keys: Vec<String> = Vec::new();
    let mut merged: Vec<String> = Vec::new();
    for requirement in results.iter().flatten() {
        let key = norm(requirement);
        match keys.iter().position(|k| *k == key) {
            Some(i) => merged[i] = requirement.clone(),
            None => {
                keys.push(key);
                merged.push(requirement.clone());
            }
        }
    }
    merged.truncate(MAX_REQUIREMENTS);
    (merged, results.iter().filter(|r| !r.is_empty()).count())
}
