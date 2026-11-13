//! Pure: shape detection. Walks the JSON in key order and lays each value out as a section.
//!
//! Rules: a long string is a titled paragraph (Markdown only for `*_markdown`, `notes`, `formatted`);
//! short scalars next to each other share one "Details" section; a list of scalars is bullets; a list of
//! flat objects is a table (at most 6 distinct keys) or cards; nested objects are numbered cards; an object
//! is key/value lines. A value nested deeper than `MAX_DEPTH` is shown as JSON. Nothing is dropped except an
//! `items` list that repeats another field.

use serde_json::{Map, Value};

use crate::model::{Body, Card, Cell, Document, Section, header, is_scalar};
use crate::templates::{self, Hint, Template};

pub const MAX_DEPTH: usize = 6;
const MAX_TABLE_KEYS: usize = 6;
const SHORT_SCALAR_CHARS: usize = 80;
const SHORT_LIST_ITEMS: usize = 8;
pub const FOOTER_KEYS: [&str; 2] = ["confidence", "needs_human"];
pub const MARKDOWN_KEYS: [&str; 3] = ["notes", "memo_markdown", "formatted"];
const TITLE_KEYS: [&str; 9] = [
    "title", "name", "task", "story", "text", "decision", "to_whom", "option", "term",
];

pub fn is_markdown_key(key: &str) -> bool {
    key.ends_with("_markdown") || MARKDOWN_KEYS.contains(&key)
}

pub fn build_document(value: &Value) -> Document {
    match value {
        Value::Object(map) => from_object(map),
        Value::Array(_) => Document {
            sections: vec![section_for("items", value, "Items", Hint::None, &[])],
            lead: None,
            footer: None,
            needs_human: false,
            template: None,
            deep: usize::from(depth(value) > MAX_DEPTH),
        },
        scalar => Document {
            sections: vec![Section::new(
                "Value",
                Body::Paragraph {
                    text: scalar.clone(),
                    markdown: false,
                },
            )],
            lead: None,
            footer: None,
            needs_human: false,
            template: None,
            deep: 0,
        },
    }
}

pub fn depth(value: &Value) -> usize {
    match value {
        Value::Object(map) => 1 + map.values().map(depth).max().unwrap_or(0),
        Value::Array(items) => 1 + items.iter().map(depth).max().unwrap_or(0),
        _ => 0,
    }
}

fn from_object(value: &Map<String, Value>) -> Document {
    let lead = value
        .get("summary")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let footer_given: Vec<&str> = FOOTER_KEYS
        .into_iter()
        .filter(|k| value.get(*k).is_some_and(is_scalar))
        .collect();
    let footer = (!footer_given.is_empty()).then(|| {
        let get = |k: &str| value.get(k).cloned().unwrap_or(Value::Null);
        (get("confidence"), get("needs_human"))
    });
    let body: Map<String, Value> = value
        .iter()
        .filter(|(k, _)| {
            !(k.as_str() == "summary" && lead.is_some()) && !footer_given.contains(&k.as_str())
        })
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let body = without_repeated_items(body);
    let template = templates::find(&body);
    let sections = match template {
        Some(t) => templated(&body, t),
        None => generic(&body),
    };
    Document {
        sections,
        lead,
        footer,
        needs_human: value.get("needs_human") == Some(&Value::Bool(true)),
        template: template.map(|t| t.name),
        deep: body.values().filter(|v| depth(v) > MAX_DEPTH).count(),
    }
}

fn without_repeated_items(body: Map<String, Value>) -> Map<String, Value> {
    let repeated = match body.get("items") {
        Some(items @ Value::Array(_)) => body.iter().any(|(k, v)| k != "items" && v == items),
        _ => false,
    };
    if !repeated {
        return body;
    }
    body.into_iter().filter(|(k, _)| k != "items").collect()
}

fn templated(body: &Map<String, Value>, template: &Template) -> Vec<Section> {
    let mut sections: Vec<Section> = template
        .slots
        .iter()
        .filter_map(|s| {
            body.get(s.key)
                .map(|v| section_for(s.key, v, s.heading, s.hint, s.columns))
        })
        .collect();
    let leftover: Vec<(String, Cell)> = body
        .iter()
        .filter(|(k, _)| !template.slots.iter().any(|s| s.key == k.as_str()))
        .map(|(k, v)| (k.clone(), to_cell(v)))
        .collect();
    if !leftover.is_empty() {
        sections.push(Section::new("Other fields", Body::Fields(leftover)));
    }
    sections
}

fn generic(body: &Map<String, Value>) -> Vec<Section> {
    let mut sections = Vec::new();
    let mut details: Vec<(String, Cell)> = Vec::new();
    for (key, value) in body {
        if is_short_scalar(value) {
            details.push((key.clone(), Cell::Scalar(value.clone())));
            continue;
        }
        if !details.is_empty() {
            sections.push(Section::new(
                "Details",
                Body::Fields(std::mem::take(&mut details)),
            ));
        }
        sections.push(section_for(key, value, &header(key), Hint::None, &[]));
    }
    if !details.is_empty() {
        sections.push(Section::new("Details", Body::Fields(details)));
    }
    sections
}

fn is_short_scalar(value: &Value) -> bool {
    match value {
        Value::String(s) => s.chars().count() <= SHORT_SCALAR_CHARS && !s.contains('\n'),
        other => is_scalar(other),
    }
}

pub fn section_for(key: &str, value: &Value, title: &str, hint: Hint, columns: &[&str]) -> Section {
    if depth(value) > MAX_DEPTH {
        return Section::new(title, Body::JsonBlock(value.clone()));
    }
    match value {
        Value::Object(map) => Section::new(
            title,
            Body::Fields(map.iter().map(|(k, v)| (k.clone(), to_cell(v))).collect()),
        ),
        Value::Array(items) => list_section(items, title, hint, columns),
        scalar => {
            let markdown = scalar.is_string() && (hint == Hint::Markdown || is_markdown_key(key));
            Section::new(
                title,
                Body::Paragraph {
                    text: scalar.clone(),
                    markdown,
                },
            )
        }
    }
}

fn list_section(values: &[Value], title: &str, hint: Hint, columns: &[&str]) -> Section {
    if values.iter().all(is_scalar) {
        return Section::new(
            title,
            Body::Bullets(values.iter().cloned().map(Cell::Scalar).collect()),
        );
    }
    let objects: Vec<&Map<String, Value>> = values.iter().filter_map(Value::as_object).collect();
    if objects.len() != values.len() {
        return Section::new(title, Body::Bullets(values.iter().map(to_cell).collect()));
    }
    let keys = ordered_keys(&objects, columns);
    if hint != Hint::Cards && objects.iter().all(|v| is_flat(v)) && keys.len() <= MAX_TABLE_KEYS {
        let rows = objects
            .iter()
            .map(|v| {
                keys.iter()
                    .map(|k| v.get(k).map_or(Cell::Scalar(Value::Null), to_cell))
                    .collect()
            })
            .collect();
        return Section::new(
            title,
            Body::Table {
                columns: keys,
                rows,
            },
        );
    }
    Section::new(
        title,
        Body::Cards(objects.iter().map(|v| card(v)).collect()),
    )
}

fn ordered_keys(values: &[&Map<String, Value>], preferred: &[&str]) -> Vec<String> {
    let mut seen: Vec<String> = Vec::new();
    for value in values {
        for key in value.keys() {
            if !seen.contains(key) {
                seen.push(key.clone());
            }
        }
    }
    let first: Vec<String> = preferred
        .iter()
        .filter(|k| seen.iter().any(|s| s == *k))
        .map(|k| (*k).to_owned())
        .collect();
    let rest = seen.into_iter().filter(|k| !first.contains(k));
    first.iter().cloned().chain(rest).collect()
}

fn is_flat(value: &Map<String, Value>) -> bool {
    !value.is_empty() && value.values().all(|v| is_scalar(v) || is_short_list(v))
}

fn is_short_list(value: &Value) -> bool {
    value
        .as_array()
        .is_some_and(|items| items.len() <= SHORT_LIST_ITEMS && items.iter().all(is_scalar))
}

fn card(value: &Map<String, Value>) -> Card {
    let title_key = TITLE_KEYS
        .into_iter()
        .find(|k| value.get(*k).is_some_and(|v| is_scalar(v) && !v.is_null()));
    let lines = value
        .iter()
        .filter(|(k, _)| Some(k.as_str()) != title_key)
        .map(|(k, v)| (k.clone(), to_cell(v)))
        .collect();
    Card {
        title: title_key.and_then(|k| value.get(k).cloned()),
        lines,
    }
}

pub fn to_cell(value: &Value) -> Cell {
    match value {
        Value::Array(items) if items.iter().all(is_scalar) => Cell::List(items.clone()),
        Value::Array(_) | Value::Object(_) => Cell::Json(value.clone()),
        scalar => Cell::Scalar(scalar.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;
    use crate::model::Kind;
    use serde_json::json;

    fn titles(doc: &Document) -> Vec<&str> {
        doc.sections.iter().map(|s| s.title.as_str()).collect()
    }

    #[test]
    fn summary_template_order_and_items_dropped_when_repeated() {
        let doc = build_document(&fixtures::thread_untangler());
        assert_eq!(doc.template, Some("summary"));
        assert_eq!(
            titles(&doc),
            [
                "TL;DR",
                "Decisions",
                "Open questions",
                "Action items",
                "Reversals"
            ]
        );
        assert_eq!(
            doc.lead.as_deref(),
            fixtures::thread_untangler()["summary"].as_str()
        );
        assert_eq!(doc.footer, Some((json!("medium"), json!(false))));
        assert_eq!(doc.sections[3].kind(), Kind::Table);
    }

    #[test]
    fn each_catalog_template() {
        assert_eq!(
            build_document(&fixtures::promises()).template,
            Some("promises")
        );
        let ranked = build_document(&fixtures::ranked());
        assert_eq!(ranked.template, Some("ranked"));
        let Body::Table { columns, .. } = &ranked.sections[0].body else {
            panic!("a table")
        };
        assert_eq!(columns, &["rank", "task", "score", "reason"]);
        let mortem = build_document(&fixtures::pre_mortem());
        assert_eq!(mortem.template, Some("pre-mortem"));
        let Body::Cards(cards) = &mortem.sections[0].body else {
            panic!("cards")
        };
        assert_eq!(cards[0].title, Some(json!("Payments outage at peak")));
        assert_eq!(
            titles(&build_document(&fixtures::draft())),
            ["Draft", "Shorter version", "Tone notes"]
        );
        let memo = build_document(&fixtures::memo());
        assert_eq!(memo.template, Some("memo"));
        assert!(matches!(
            memo.sections[0].body,
            Body::Paragraph { markdown: true, .. }
        ));
        assert_eq!(titles(&memo).last(), Some(&"Other fields"));
        assert_eq!(
            build_document(&json!({"translation": "Hola", "notes": "n"})).template,
            Some("translation")
        );
        assert_eq!(
            build_document(&json!({"text": "t", "ai_pattern_density": 0.2})).template,
            Some("text")
        );
    }

    #[test]
    fn unknown_keys_land_in_other_fields() {
        let mut value = fixtures::ranked();
        value["owner_team"] = json!("growth");
        value["extra"] = json!({"k": [1, 2]});
        let doc = build_document(&value);
        let other = doc.sections.last().expect("a section");
        let Body::Fields(lines) = &other.body else {
            panic!("fields")
        };
        assert_eq!(other.title, "Other fields");
        assert_eq!(
            lines.iter().map(|(k, _)| k.as_str()).collect::<Vec<_>>(),
            ["owner_team", "extra"]
        );
    }

    #[test]
    fn generic_shapes() {
        let doc = build_document(
            &json!({"a": 1, "b": "short", "long": "x ".repeat(60), "tags": ["p", "q"],
            "rows": [{"k": 1, "v": "a"}], "nested": [{"name": "n", "deep": {"x": 1}}], "obj": {"x": 1, "y": [1, 2]}}),
        );
        assert_eq!(doc.template, None);
        let got: Vec<(&str, Kind)> = doc
            .sections
            .iter()
            .map(|s| (s.title.as_str(), s.kind()))
            .collect();
        assert_eq!(
            got,
            [
                ("Details", Kind::Fields),
                ("Long", Kind::Paragraph),
                ("Tags", Kind::Bullets),
                ("Rows", Kind::Table),
                ("Nested", Kind::Cards),
                ("Obj", Kind::Fields)
            ]
        );
    }

    #[test]
    fn wide_flat_objects_are_cards_not_tables() {
        let row: Map<String, Value> = (0..7).map(|i| (format!("k{i}"), json!(i))).collect();
        assert_eq!(
            build_document(&json!({"rows": [row]})).sections[0].kind(),
            Kind::Cards
        );
    }

    #[test]
    fn depth_over_six_is_a_json_block() {
        let doc = build_document(
            &json!({"summary": "s", "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}),
        );
        assert_eq!(doc.sections[0].kind(), Kind::JsonBlock);
        assert_eq!(doc.deep, 1);
    }

    #[test]
    fn items_kept_when_it_is_the_only_list() {
        assert_eq!(
            titles(&build_document(
                &json!({"summary": "s", "items": ["a", "b"]})
            )),
            ["Items"]
        );
    }

    #[test]
    fn key_order_is_stable() {
        let doc = build_document(&json!({"rows": [{"z": 1, "a": 2}, {"m": 3, "z": 4}]}));
        let Body::Table { columns, .. } = &doc.sections[0].body else {
            panic!("a table")
        };
        assert_eq!(columns, &["z", "a", "m"]);
    }
}
