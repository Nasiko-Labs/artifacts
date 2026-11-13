//! Pure: user text and pasted specs are data. Each block sent to the model is fenced between
//! `<<<NAME>>>` and `>>>END_NAME<<<` markers, and any marker already inside the text is stripped first,
//! so a pasted spec cannot close its own fence and talk to the model as instructions.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::re;

static MARKER: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<"));

pub fn strip_markers(text: &str) -> String {
    MARKER.replace_all(text, "").into_owned()
}

pub fn fence(name: &str, text: &str) -> String {
    format!("<<<{name}>>>\n{}\n>>>END_{name}<<<", strip_markers(text))
}
