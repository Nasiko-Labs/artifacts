//! Pure: a previous workflow step's envelope (JSON with `items[]` = tasks) read back as the current plan.
//! Code re-checks it (no model call); free text next to it is the change to make.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::checks::normalize;
use crate::finalize::{Context, finalize};
use crate::models::{Plan, Task};
use crate::planning::REVISION_BAND;
use crate::util::re;

static FENCED: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)```(?:json)?\s*(\{.*\})\s*```"));
static TASK_ID: LazyLock<Regex> = LazyLock::new(|| re(r"^T\d+$"));
pub const READ_NOTE: &str = "Read the plan from the previous step's json.";

/// (envelope, the rest of the message), or None when the message holds no envelope.
pub fn find(text: &str) -> Option<(Map<String, Value>, String)> {
    let (span, body) = match FENCED.captures(text) {
        Some(caps) => {
            let whole = caps.get(0)?;
            (whole.range(), caps.get(1)?.as_str())
        }
        None => {
            let start = text.find('{')?;
            let end = text.rfind('}')? + 1;
            if end <= start {
                return None;
            }
            (start..end, &text[start..end])
        }
    };
    let data: Value = serde_json::from_str(body).ok()?;
    let map = data.as_object()?;
    let items = map.get("items")?.as_array()?;
    if items.is_empty()
        || !items
            .iter()
            .all(|i| i.as_object().is_some_and(|o| o.contains_key("title")))
    {
        return None;
    }
    let rest = format!("{}{}", &text[..span.start], &text[span.end..])
        .trim()
        .to_string();
    Some((map.clone(), rest))
}

fn has_ids(tasks: &[Task]) -> bool {
    tasks.iter().all(|t| TASK_ID.is_match(&t.id))
}

fn text_or(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

pub fn to_plan(env: &Map<String, Value>, raw_text: &str) -> Plan {
    let phases: Vec<Value> = env
        .get("phases")
        .and_then(Value::as_array)
        .map(|ps| {
            ps.iter()
                .filter_map(Value::as_object)
                .map(|p| json!({"id": p.get("number"), "name": p.get("name")}))
                .collect()
        })
        .unwrap_or_default();
    let list = |key: &str| {
        env.get(key)
            .filter(|v| v.is_array())
            .cloned()
            .unwrap_or_else(|| json!([]))
    };
    let raw = json!({"phases": phases, "tasks": list("items"), "risks": list("risks"),
                     "assumptions": list("assumptions"), "constraints": list("constraints")});
    let draft = normalize(raw.as_object().unwrap_or(&Map::new()));
    let goal = [text_or(env.get("goal")), text_or(env.get("summary"))]
        .into_iter()
        .find(|g| !g.is_empty())
        .unwrap_or_else(|| "the plan from the previous step".into());
    let ctx = Context {
        renumber: !has_ids(&draft.tasks),
        deadline: text_or(env.get("deadline")),
        notes: vec![READ_NOTE.into()],
        ..Context::new(&goal, raw_text, REVISION_BAND)
    };
    finalize(&draft, &ctx)
}
