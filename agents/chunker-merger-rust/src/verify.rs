//! Pure: the lossless check every split must pass before it is shown. The parts (without their overlap)
//! must rejoin to the input exactly, be contiguous and non-empty, and stay within the part cap.

use crate::splitters::Span;
use crate::textidx::Text;

pub const MAX_PARTS: usize = 40;

/// None when the split is sound, else what is wrong with it.
pub fn problem(text: &Text, spans: &[Span]) -> Option<String> {
    let (Some(first), Some(last)) = (spans.first(), spans.last()) else {
        return Some("no parts".to_owned());
    };
    if spans.len() > MAX_PARTS {
        return Some(format!(
            "{} parts is over the cap of {MAX_PARTS}",
            spans.len()
        ));
    }
    if first.start != 0 || last.end != text.len() {
        return Some("the parts don't cover the whole text".to_owned());
    }
    if spans.windows(2).any(|w| w[1].start != w[0].end) {
        return Some("the parts are not contiguous".to_owned());
    }
    for span in spans {
        if span.end <= span.start || text.slice(span.start, span.end).trim().is_empty() {
            return Some("a part is empty".to_owned());
        }
        if span.view_start > span.start {
            return Some("an overlap reaches outside the text".to_owned());
        }
    }
    let rejoined: String = spans.iter().map(|s| text.slice(s.start, s.end)).collect();
    if rejoined != text.s {
        return Some("the parts don't rejoin to the input".to_owned());
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn span(start: usize, end: usize) -> Span {
        Span {
            start,
            end,
            view_start: start,
            mid: false,
        }
    }

    #[test]
    fn names_each_problem() {
        let text = Text::new("abcdef");
        assert_eq!(problem(&text, &[span(0, 3), span(3, 6)]), None);
        assert!(
            problem(&text, &[span(0, 3), span(4, 6)]).is_some_and(|p| p.contains("contiguous"))
        );
        assert!(problem(&text, &[span(0, 3)]).is_some_and(|p| p.contains("cover")));
        assert_eq!(problem(&text, &[]).as_deref(), Some("no parts"));
    }
}
