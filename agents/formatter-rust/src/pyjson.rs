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
    fn strings_keep_non_ascii() {
        assert_eq!(string("café \"q\""), "\"café \\\"q\\\"\"");
    }
}
