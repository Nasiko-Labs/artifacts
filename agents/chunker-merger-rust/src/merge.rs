//! Pure: merge two or more same-schema JSON results (parts of one run) into one result with that schema.
//! Structural only: lists are concatenated (each item tagged with its `part`) and exact duplicates removed,
//! `summary`/`tldr`/`notes` strings are stitched and labelled, counts on a fixed list are summed, objects are
//! deep-merged, `confidence` is the lowest and `needs_human` the OR. No item's text is ever rewritten.

use std::collections::HashSet;

use serde_json::{Map, Value, json};

use crate::dedup;
use crate::pyjson;

const LEVELS: [&str; 3] = ["low", "medium", "high"];
const SUM_KEYS: [&str; 5] = ["words", "count", "total", "dropped", "left_out"];
const STITCH_KEYS: [&str; 3] = ["summary", "tldr", "notes"];
const RANKED_KEYS: [&str; 2] = ["ranked", "failure_modes"];
const OWN_KEYS: [&str; 5] = [
    "confidence",
    "needs_human",
    "merge_notes",
    "merged_parts",
    "items_possibly_duplicate",
];
const MIN_JACCARD: f64 = 0.5;
const SUMMARY_MAX_CHARS: usize = 600;

type Object = Map<String, Value>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    NeedMore,
    Different,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MergeResult {
    pub status: Status,
    pub objects: Vec<Object>,
    pub envelope: Option<Value>,
    pub notes: Vec<String>,
}

pub fn merge(values: &[Value], problems: &[String]) -> MergeResult {
    let mut notes: Vec<String> = problems.to_vec();
    let mut objects = Vec::new();
    for (n, value) in values.iter().enumerate() {
        match value {
            Value::Object(map) => objects.push(map.clone()),
            other => notes.push(format!(
                "Result {} is not a JSON object ({}); ignored.",
                n + 1,
                pyjson::type_name(other)
            )),
        }
    }
    if objects.len() < 2 {
        return MergeResult {
            status: Status::NeedMore,
            objects,
            envelope: None,
            notes,
        };
    }
    if !same_schema(&objects) {
        return MergeResult {
            status: Status::Different,
            objects,
            envelope: None,
            notes,
        };
    }
    let (mut merged, uncertain) = merge_fields(&objects, &mut notes);
    if objects.iter().any(|o| o.contains_key("confidence")) {
        merged.insert(
            "confidence".into(),
            json!(confidence(&objects, &mut notes, uncertain)),
        );
    }
    let needs_human = objects
        .iter()
        .any(|o| o.get("needs_human").is_some_and(pyjson::truthy))
        || objects.len() < values.len()
        || !problems.is_empty();
    merged.insert("needs_human".into(), json!(needs_human));
    merged.insert("merged_parts".into(), json!(objects.len()));
    merged.insert("merge_notes".into(), json!(notes));
    MergeResult {
        status: Status::Ok,
        objects,
        envelope: Some(Value::Object(merged)),
        notes,
    }
}

fn schema_keys(object: &Object) -> HashSet<&str> {
    object
        .keys()
        .map(String::as_str)
        .filter(|k| !OWN_KEYS.contains(k))
        .collect()
}

pub fn same_schema(objects: &[Object]) -> bool {
    let Some(first) = objects.first().map(schema_keys) else {
        return true;
    };
    objects[1..].iter().all(|other| {
        let keys = schema_keys(other);
        let union = first.union(&keys).count();
        union == 0 || (first.intersection(&keys).count() as f64 / union as f64) >= MIN_JACCARD
    })
}

fn ordered_keys(objects: &[&Object]) -> Vec<String> {
    let mut keys: Vec<String> = Vec::new();
    for object in objects {
        for key in object.keys() {
            if !keys.contains(key) {
                keys.push(key.clone());
            }
        }
    }
    keys
}

fn merge_fields(objects: &[Object], notes: &mut Vec<String>) -> (Object, bool) {
    let alias = items_alias(objects);
    let refs: Vec<&Object> = objects.iter().collect();
    let mut out = Object::new();
    let mut uncertain = false;
    let mut near_pairs = 0;
    for key in ordered_keys(&refs) {
        if OWN_KEYS.contains(&key.as_str()) || (key == "items" && alias.is_some()) {
            continue;
        }
        let present: Vec<(usize, &Value)> = objects
            .iter()
            .enumerate()
            .filter_map(|(n, o)| o.get(&key).map(|v| (n + 1, v)))
            .collect();
        let values: Vec<&Value> = present.iter().map(|(_, v)| *v).collect();
        let merged = if values.iter().all(|v| v.is_array()) {
            let (items, near) = merge_list(&key, &present, notes);
            near_pairs += near;
            items
        } else if values.iter().all(|v| v.is_string()) && STITCH_KEYS.contains(&key.as_str()) {
            Value::String(stitch(&key, &present, objects.len()))
        } else if values.iter().all(|v| v.is_number()) {
            let (number, fell_back) = merge_numbers(&key, &values, notes);
            uncertain = uncertain || fell_back;
            number
        } else if values.iter().all(|v| v.is_object()) {
            let dicts: Vec<&Object> = values.iter().filter_map(|v| v.as_object()).collect();
            Value::Object(deep_merge(&dicts, &key, notes))
        } else {
            first_wins(&key, &values, notes)
        };
        out.insert(key, merged);
    }
    if let Some(alias) = alias {
        let items = out.get(&alias).cloned().unwrap_or_else(|| json!([]));
        out.insert("items".into(), items);
    }
    if near_pairs > 0 {
        out.insert("items_possibly_duplicate".into(), json!(near_pairs));
        notes.push(format!(
            "possible duplicates: {near_pairs} pair(s) (kept; check them)"
        ));
    }
    (out, uncertain || near_pairs > 0)
}

/// The named list every result's `items` equals, when it is the same name in all of them.
fn items_alias(objects: &[Object]) -> Option<String> {
    let mut names = HashSet::new();
    for object in objects {
        let items = object.get("items")?;
        let name = object
            .iter()
            .find(|(k, v)| k.as_str() != "items" && v.is_array() && *v == items)?
            .0;
        names.insert(name.clone());
    }
    if names.len() == 1 {
        names.into_iter().next()
    } else {
        None
    }
}

fn merge_list(key: &str, present: &[(usize, &Value)], notes: &mut Vec<String>) -> (Value, usize) {
    let joined: Vec<Value> = present
        .iter()
        .flat_map(|(n, items)| {
            items
                .as_array()
                .into_iter()
                .flatten()
                .map(move |item| tag(item, *n))
        })
        .collect();
    let result = dedup::dedup(joined);
    if result.removed > 0 {
        notes.push(format!("{} duplicate {key} removed", result.removed));
    }
    let mut items = result.items;
    if RANKED_KEYS.contains(&key) {
        for (i, item) in items.iter_mut().enumerate() {
            if let Value::Object(map) = item {
                map.insert("rank".into(), json!(i + 1));
            }
        }
        notes.push(format!(
            "{key} re-numbered in paste order; scores from different parts were not re-compared (re-run the Prioritizer for a true ranking)"
        ));
    }
    (Value::Array(items), result.possible_pairs)
}

fn tag(item: &Value, part: usize) -> Value {
    match item {
        Value::Object(map) if !map.contains_key("part") => {
            let mut tagged = map.clone();
            tagged.insert("part".into(), json!(part));
            Value::Object(tagged)
        }
        other => other.clone(),
    }
}

fn stitch(key: &str, present: &[(usize, &Value)], total: usize) -> String {
    let text = present
        .iter()
        .filter_map(|(n, v)| {
            v.as_str()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(|s| format!("Part {n}: {s}"))
        })
        .collect::<Vec<_>>()
        .join("\n\n");
    if key != "summary" {
        return text;
    }
    let head = format!("Merged {total} parts (stitched, not rewritten): ");
    let room = SUMMARY_MAX_CHARS - head.chars().count();
    if text.chars().count() <= room {
        return format!("{head}{text}");
    }
    let cut: String = text.chars().take(room - 1).collect();
    format!("{head}{}…", cut.trim_end())
}

fn merge_numbers(key: &str, values: &[&Value], notes: &mut Vec<String>) -> (Value, bool) {
    if SUM_KEYS.contains(&key) {
        let ints: Option<Vec<i64>> = values.iter().map(|v| v.as_i64()).collect();
        let summed = ints
            .and_then(|ints| ints.into_iter().try_fold(0i64, i64::checked_add))
            .map(Value::from)
            .unwrap_or_else(|| json!(values.iter().filter_map(|v| v.as_f64()).sum::<f64>()));
        return (summed, false);
    }
    let first = values.first().and_then(|v| v.as_f64());
    if values.iter().all(|v| v.as_f64() == first) {
        return (values.first().map_or(Value::Null, |v| (*v).clone()), false);
    }
    notes.push(format!(
        "{key} differs across parts and isn't a count, so it is kept as a list"
    ));
    (
        Value::Array(values.iter().map(|v| (*v).clone()).collect()),
        true,
    )
}

fn deep_merge(dicts: &[&Object], path: &str, notes: &mut Vec<String>) -> Object {
    let mut out = Object::new();
    for key in ordered_keys(dicts) {
        let values: Vec<&Value> = dicts.iter().filter_map(|d| d.get(&key)).collect();
        let merged = if values.iter().all(|v| v.is_array()) {
            Value::Array(
                values
                    .iter()
                    .filter_map(|v| v.as_array())
                    .flatten()
                    .cloned()
                    .collect(),
            )
        } else if values.iter().all(|v| v.is_object()) {
            let inner: Vec<&Object> = values.iter().filter_map(|v| v.as_object()).collect();
            Value::Object(deep_merge(&inner, &format!("{path}.{key}"), notes))
        } else {
            first_wins(&format!("{path}.{key}"), &values, notes)
        };
        out.insert(key, merged);
    }
    out
}

fn first_wins(key: &str, values: &[&Value], notes: &mut Vec<String>) -> Value {
    let present: Vec<&Value> = values.iter().copied().filter(|v| !v.is_null()).collect();
    let Some(first) = present.first() else {
        return Value::Null;
    };
    if present[1..].iter().any(|v| v != first) {
        notes.push(format!("{key} differs across parts; kept the first"));
    }
    (*first).clone()
}

fn confidence(objects: &[Object], notes: &mut Vec<String>, uncertain: bool) -> &'static str {
    let mut lowest = LEVELS.len() - 1;
    for (n, object) in objects.iter().enumerate() {
        let given = object
            .get("confidence")
            .filter(|v| pyjson::truthy(v))
            .map(pyjson::py_str)
            .unwrap_or_default();
        let level = LEVELS
            .iter()
            .position(|l| *l == given.to_lowercase())
            .unwrap_or_else(|| {
                notes.push(format!(
                    "Part {} had no usable confidence; counted as medium.",
                    n + 1
                ));
                1
            });
        lowest = lowest.min(level);
    }
    LEVELS[if uncertain { lowest.min(1) } else { lowest }]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;

    fn ok(values: Vec<Value>) -> Value {
        let result = merge(&values, &[]);
        assert_eq!(result.status, Status::Ok, "{result:?}");
        result.envelope.expect("an envelope")
    }

    fn notes_of(merged: &Value) -> Vec<String> {
        serde_json::from_value(merged["merge_notes"].clone()).unwrap_or_default()
    }

    #[test]
    fn three_summarizer_parts_merge_into_one_envelope_with_the_same_schema() {
        let parts = vec![
            fixtures::part(1),
            fixtures::summarizer_part(2, "low", false),
            fixtures::summarizer_part(3, "high", true),
        ];
        let merged = ok(parts.clone());
        assert!(
            parts[0]
                .as_object()
                .is_some_and(|p| p.keys().all(|k| merged.get(k).is_some()))
        );
        assert_eq!(
            (
                merged["confidence"].as_str(),
                merged["needs_human"].as_bool(),
                merged["merged_parts"].as_u64()
            ),
            (Some("low"), Some(true), Some(3))
        );
        let decisions: Vec<&str> = merged["decisions"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|d| d["text"].as_str())
            .collect();
        assert_eq!(
            decisions,
            [
                "Keep the mid tier at $49",
                "Decision 1",
                "Decision 2",
                "Decision 3"
            ]
        );
        let notes = notes_of(&merged);
        assert!(
            notes.contains(&"2 duplicate decisions removed".to_owned())
                && notes.contains(&"2 duplicate action_items removed".to_owned())
        );
        let parts_of: Vec<u64> = merged["action_items"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|a| a["part"].as_u64())
            .collect();
        assert_eq!(parts_of, [1, 1, 2, 3]);
        assert_eq!(merged["items"], merged["action_items"]);
        let tldr = merged["tldr"].as_str().unwrap_or_default();
        assert!(tldr.starts_with("Part 1: In part 1") && tldr.contains("\n\nPart 3: "));
        assert!(merged["summary"].as_str().is_some_and(|s| s.starts_with(
            "Merged 3 parts (stitched, not rewritten): Part 1: Part 1 of the pricing call."
        )));
        assert_eq!(merged["words"], 6000);
    }

    #[test]
    fn existing_part_tags_are_kept_and_text_never_changes() {
        let merged = ok(vec![
            json!({"items": [{"text": "Alpha", "part": 9}], "confidence": "high"}),
            json!({"items": [{"text": "Beta"}], "confidence": "high"}),
        ]);
        assert_eq!(
            merged["items"],
            json!([{"text": "Alpha", "part": 9}, {"text": "Beta", "part": 2}])
        );
    }

    #[test]
    fn numbers_off_the_sum_list_become_a_list_and_lower_confidence() {
        let merged = ok(vec![
            json!({"score": 3, "count": 2, "confidence": "high"}),
            json!({"score": 5, "count": 4, "confidence": "high"}),
        ]);
        assert_eq!(
            (
                merged["count"].clone(),
                merged["score"].clone(),
                merged["confidence"].clone()
            ),
            (json!(6), json!([3, 5]), json!("medium"))
        );
        assert!(
            notes_of(&merged)
                .iter()
                .any(|n| n.contains("score differs"))
        );
    }

    #[test]
    fn objects_deep_merge_and_scalars_first_wins_with_a_note() {
        let merged = ok(vec![
            json!({"meta": {"tags": ["a"], "owner": "Lee", "inner": {"x": 1}}, "flag": null, "done": true}),
            json!({"meta": {"tags": ["b"], "owner": "Sam", "inner": {"y": 2}}, "flag": false, "done": true}),
        ]);
        assert_eq!(
            merged["meta"],
            json!({"tags": ["a", "b"], "owner": "Lee", "inner": {"x": 1, "y": 2}})
        );
        assert_eq!(
            (merged["flag"].clone(), merged["done"].clone()),
            (json!(false), json!(true))
        );
        assert!(
            notes_of(&merged)
                .iter()
                .any(|n| n.contains("meta.owner differs"))
        );
    }

    #[test]
    fn missing_confidence_counts_as_medium_with_a_note() {
        let merged = ok(vec![
            json!({"items": [], "confidence": "high"}),
            json!({"items": []}),
        ]);
        assert_eq!(merged["confidence"], "medium");
        assert!(
            notes_of(&merged)
                .contains(&"Part 2 had no usable confidence; counted as medium.".to_owned())
        );
    }

    #[test]
    fn overlap_duplicates_by_quote_are_removed() {
        let merged = ok(vec![
            json!({"items": [{"quote": "We ship Friday", "text": "Ship Friday"}], "confidence": "high"}),
            json!({"items": [{"quote": "We ship Friday", "text": "Ship on Friday"}], "confidence": "high"}),
        ]);
        assert_eq!(merged["items"].as_array().map(Vec::len), Some(1));
        assert_eq!(merged["items"][0]["part"], 1);
    }

    #[test]
    fn possible_duplicates_are_reported_not_removed() {
        let merged = ok(vec![
            json!({"items": [{"text": "update the pricing page for the new mid tier plan today please now"}], "confidence": "high"}),
            json!({"items": [{"text": "update the pricing page for the new mid tier plan today please soon"}], "confidence": "high"}),
        ]);
        assert_eq!(
            (
                merged["items"].as_array().map(Vec::len),
                merged["items_possibly_duplicate"].clone()
            ),
            (Some(2), json!(1))
        );
        assert_eq!(merged["confidence"], "medium");
        assert!(
            notes_of(&merged)
                .iter()
                .any(|n| n.starts_with("possible duplicates: 1 pair"))
        );
    }

    #[test]
    fn ranked_lists_are_renumbered_with_a_note() {
        let merged = ok(vec![fixtures::ranked_1(), fixtures::ranked_2()]);
        let ranks: Vec<(String, u64)> = merged["ranked"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|r| {
                (
                    r["task"].as_str().unwrap_or_default().to_owned(),
                    r["rank"].as_u64().unwrap_or_default(),
                )
            })
            .collect();
        assert_eq!(
            ranks,
            [
                ("Fix login".to_owned(), 1),
                ("Write plan".to_owned(), 2),
                ("Hire SRE".to_owned(), 3)
            ]
        );
        assert!(
            notes_of(&merged)
                .iter()
                .any(|n| n.contains("re-numbered") && n.contains("Prioritizer"))
        );
    }

    #[test]
    fn schema_mismatch_is_refused() {
        let result = merge(&[fixtures::part(1), fixtures::ranked_1()], &[]);
        assert!(result.status == Status::Different && result.envelope.is_none());
    }

    #[test]
    fn non_objects_are_ignored_by_position_and_fewer_than_two_need_more() {
        let result = merge(&[fixtures::ranked_1(), json!([1, 2]), json!("text")], &[]);
        assert!(result.status == Status::NeedMore && result.objects.len() == 1);
        assert_eq!(
            result.notes,
            [
                "Result 2 is not a JSON object (list); ignored.",
                "Result 3 is not a JSON object (str); ignored."
            ]
        );
        let merged = ok(vec![fixtures::ranked_1(), json!(7), fixtures::ranked_2()]);
        assert_eq!(
            (
                merged["needs_human"].clone(),
                merged["merged_parts"].clone()
            ),
            (json!(true), json!(2))
        );
    }

    #[test]
    fn thread_untangler_parts_merge() {
        let merged = ok(vec![
            fixtures::thread_untangler(),
            fixtures::thread_untangler_2(),
        ]);
        assert_eq!(
            merged["tldr"],
            json!([
                "Mid tier stays at $49 per seat.",
                "Starter plan is dropped from the page.",
                "SOW review has no owner."
            ])
        );
        let parts: Vec<u64> = merged["open_questions"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|q| q["part"].as_u64())
            .collect();
        assert_eq!(parts, [1, 2]);
        assert!(
            merged["items"] == merged["action_items"]
                && merged["items"].as_array().map(Vec::len) == Some(2)
        );
        assert_eq!(
            (
                merged["confidence"].clone(),
                merged["needs_human"].clone(),
                merged["reversals"].clone()
            ),
            (json!("medium"), json!(true), json!([]))
        );
    }

    #[test]
    fn merge_is_deterministic() {
        let parts = vec![fixtures::part(1), fixtures::part(2)];
        assert_eq!(pyjson::dumps(&ok(parts.clone())), pyjson::dumps(&ok(parts)));
    }
}
