//! Pure: JSON text spelled the way the original agent spells it. Compact output uses `", "` and `": "`
//! separators (Python's `json.dumps` default); pretty output is two-space indented. Non-ASCII is kept.

use serde_json::Value;

/// One line, `{"a": 1, "b": [1, 2]}`.
pub fn dumps(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(dumps).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", string(k), dumps(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        other => other.to_string(),
    }
}

/// Two-space indented, `"key": value`.
pub fn pretty(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| dumps(value))
}

/// Like `dumps`, with every object's keys sorted (Python's `sort_keys=True`).
pub fn dumps_sorted(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(dumps_sorted).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let inner: Vec<String> = keys
                .into_iter()
                .map(|k| format!("{}: {}", string(k), dumps_sorted(&map[k])))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        other => other.to_string(),
    }
}

/// Python's `str()` of a JSON value: strings as is, `True`/`False`/`None`, numbers as JSON spells them,
/// containers as compact JSON.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Null => "None".to_owned(),
        other => dumps(other),
    }
}

/// Python's truthiness of a JSON value.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Python's type name for a JSON value (`list`, `str`, `int`, ...).
pub fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// A JSON string literal for `text`, quotes included.
pub fn string(text: &str) -> String {
    Value::String(text.to_owned()).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn compact_uses_spaced_separators_and_keeps_order() {
        let value = json!({"z": 1, "a": [1, 2.5, "x"], "o": {}});
        assert_eq!(dumps(&value), r#"{"z": 1, "a": [1, 2.5, "x"], "o": {}}"#);
    }

    #[test]
    fn pretty_is_two_space_indented() {
        assert_eq!(pretty(&json!({"a": [1]})), "{\n  \"a\": [\n    1\n  ]\n}");
    }

    #[test]
    fn sorted_keys_and_python_spellings() {
        assert_eq!(
            dumps_sorted(&json!({"b": {"d": 1, "c": 2}, "a": [true]})),
            r#"{"a": [true], "b": {"c": 2, "d": 1}}"#
        );
        assert_eq!(
            (
                py_str(&json!(true)),
                py_str(&json!(null)),
                py_str(&json!(3))
            ),
            ("True".into(), "None".into(), "3".into())
        );
        assert!(!truthy(&json!(0)) && truthy(&json!("x")) && !truthy(&json!([])));
        assert_eq!(
            (
                type_name(&json!([1])),
                type_name(&json!("s")),
                type_name(&json!(1.5))
            ),
            ("list", "str", "float")
        );
    }

    #[test]
    fn strings_keep_non_ascii() {
        assert_eq!(string("café \"q\""), "\"café \\\"q\\\"\"");
    }
}
