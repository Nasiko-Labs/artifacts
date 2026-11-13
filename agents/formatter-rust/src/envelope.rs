//! Pure: the render pipeline (detect shapes -> render the target -> limits -> fidelity check) and the
//! output envelope. A fidelity failure falls back to Markdown (then to the JSON as given), never to
//! partial output. `confidence` reflects only rendering loss; `needs_human` is copied from the input.

use serde_json::{Value, json};

use crate::convert_md;
use crate::escape;
use crate::fidelity;
use crate::limits;
use crate::model::{Document, Options, Stats, Target};
use crate::pyjson;
use crate::render_email;
use crate::render_md;
use crate::render_slack;
use crate::replies;
use crate::shapes::{self, MAX_DEPTH};

#[derive(Debug, Clone, PartialEq)]
pub struct Rendering {
    pub target: &'static str,
    pub formatted: String,
    /// Fence language in chat: "" = shown as is.
    pub lang: &'static str,
    pub stats: Stats,
    pub notes: Vec<String>,
    pub plain_text: Option<String>,
    pub subject: Option<String>,
    pub items: Vec<(String, usize)>,
    pub confidence: &'static str,
    pub needs_human: bool,
    pub summary: String,
}

impl Rendering {
    pub fn to_envelope(&self) -> Value {
        let items: Vec<Value> = self
            .items
            .iter()
            .map(|(s, rows)| json!({"section": s, "rows": rows}))
            .collect();
        json!({"summary": self.summary, "items": items, "formatted": self.formatted, "target": self.target,
               "plain_text": self.plain_text, "notes": self.notes, "confidence": self.confidence,
               "needs_human": self.needs_human})
    }
}

/// One drawing of the documents for a target.
#[derive(Debug, Clone)]
pub struct Drawn {
    pub formatted: String,
    pub lang: &'static str,
    pub stats: Stats,
    /// The output with the target's escaping undone (for the fidelity check).
    pub visible: String,
    pub plain: Option<String>,
}

/// The real renderers. Tests swap in a broken one to prove the fallback.
pub fn draw(docs: &[Document], opts: &Options) -> Drawn {
    match opts.target {
        Target::Slack => {
            let (payload, stats) = render_slack::render(docs, opts);
            let mut texts =
                render_slack::block_texts(payload["blocks"].as_array().map_or(&[], Vec::as_slice));
            texts.push(payload["text"].as_str().unwrap_or_default().to_owned());
            Drawn {
                formatted: pyjson::pretty(&payload),
                lang: "json",
                stats,
                visible: escape::slack_unescape(&texts.join("\n")),
                plain: None,
            }
        }
        Target::Email => {
            let (markup, stats) = render_email::render(docs, opts);
            let (plain, _) = render_md::render(docs, opts, true);
            Drawn {
                visible: escape::html_unescape(&markup),
                formatted: markup,
                lang: "html",
                stats,
                plain: Some(plain),
            }
        }
        Target::Markdown => {
            let (text, stats) = render_md::render(docs, opts, false);
            Drawn {
                visible: escape::md_unescape(&text),
                formatted: text,
                lang: "",
                stats,
                plain: None,
            }
        }
    }
}

pub fn render_values(values: &[Value], opts: &Options) -> Rendering {
    render_values_with(values, opts, &draw)
}

pub fn render_values_with(
    values: &[Value],
    opts: &Options,
    drawer: &dyn Fn(&[Document], &Options) -> Drawn,
) -> Rendering {
    let docs: Vec<Document> = values.iter().map(shapes::build_document).collect();
    let mut drawn = drawer(&docs, opts);
    let mut target = opts.target;
    let mut fallback_note = None;
    if !fidelity::check(values, &drawn.visible, &drawn.stats.excluded).ok() {
        tracing::error!(
            target = opts.target.name(),
            "fidelity check failed; falling back"
        );
        target = Target::Markdown;
        fallback_note = Some(replies::not_faithful(opts.target.label()));
        drawn = drawer(
            &docs,
            &Options {
                target: Target::Markdown,
                ..opts.clone()
            },
        );
        drawn.plain = None;
        if opts.target == Target::Markdown
            || !fidelity::check(values, &drawn.visible, &drawn.stats.excluded).ok()
        {
            drawn = Drawn {
                formatted: as_given(values),
                lang: "json",
                stats: Stats::default(),
                visible: String::new(),
                plain: None,
            };
            fallback_note = Some(replies::NOT_FAITHFUL_ANY.to_owned());
        }
    }
    let notes = notes(&docs, &drawn.stats, fallback_note.as_deref());
    let loss = drawn.stats.cut > 0
        || drawn.stats.collapsed > 0
        || fallback_note.is_some()
        || docs.iter().any(|d| d.deep > 0);
    let generic = docs.iter().any(|d| d.template.is_none());
    let items: Vec<(String, usize)> = docs
        .iter()
        .flat_map(|d| d.sections.iter().map(|s| (s.title.clone(), s.size())))
        .collect();
    let rows: usize = items.iter().map(|(_, n)| n).sum();
    let summary = format!(
        "Formatted {} envelope{} ({rows} item{}) as {}.",
        docs.len(),
        plural(docs.len()),
        plural(rows),
        target.label()
    );
    let lead = docs
        .first()
        .and_then(|d| d.lead.clone())
        .unwrap_or_default();
    Rendering {
        target: target.name(),
        formatted: drawn.formatted,
        lang: drawn.lang,
        stats: drawn.stats,
        notes,
        plain_text: drawn.plain,
        subject: (target == Target::Email && !lead.is_empty())
            .then(|| limits::cut(&lead, limits::SUBJECT_CHARS)),
        items,
        confidence: if loss || generic { "medium" } else { "high" },
        needs_human: docs.iter().any(|d| d.needs_human),
        summary,
    }
}

fn plural(n: usize) -> &'static str {
    if n == 1 { "" } else { "s" }
}

/// Pasted Markdown (not JSON): converted for Slack or email; a Markdown target returns it unchanged.
pub fn render_markdown(source: &str, opts: &Options) -> Rendering {
    let items = vec![("Markdown".to_owned(), 1)];
    let (formatted, lang, plain, stats, visible) = match opts.target {
        Target::Slack => {
            let chunks = limits::split_text(
                convert_md::to_mrkdwn(source).trim(),
                limits::SLACK_SECTION_CHARS,
            );
            let blocks: Vec<Value> = chunks
                .iter()
                .take(limits::SLACK_MAX_BLOCKS)
                .map(|c| render_slack::section(c))
                .collect();
            let flat = source.split_whitespace().collect::<Vec<_>>().join(" ");
            let payload =
                json!({"blocks": blocks, "text": escape::slack_entities(&limits::cut(&flat, 150))});
            let visible = escape::slack_unescape(&render_slack::block_texts(&blocks).join("\n"));
            let stats = Stats {
                blocks: blocks.len(),
                ..Stats::default()
            };
            (pyjson::pretty(&payload), "json", None, stats, visible)
        }
        Target::Email => {
            let markup = render_email::page(&convert_md::to_html(source));
            let visible = escape::html_unescape(&markup);
            (
                markup,
                "html",
                Some(source.trim().to_owned()),
                Stats::default(),
                visible,
            )
        }
        Target::Markdown => {
            let text = source.trim().to_owned();
            (text.clone(), "", None, Stats::default(), text)
        }
    };
    if !lang.is_empty() && !fidelity::words_kept(source, &visible) {
        tracing::error!(
            target = opts.target.name(),
            "markdown conversion lost words"
        );
        return Rendering {
            target: Target::Markdown.name(),
            formatted: source.trim().to_owned(),
            lang: "",
            stats: Stats::default(),
            notes: vec![replies::not_faithful(opts.target.label())],
            plain_text: None,
            subject: None,
            items,
            confidence: "medium",
            needs_human: false,
            summary: "Returned the Markdown unchanged.".to_owned(),
        };
    }
    Rendering {
        target: opts.target.name(),
        formatted,
        lang,
        stats,
        notes: Vec::new(),
        plain_text: plain,
        subject: None,
        items,
        confidence: "high",
        needs_human: false,
        summary: format!("Converted Markdown to {}.", opts.target.label()),
    }
}

fn as_given(values: &[Value]) -> String {
    values
        .iter()
        .map(pyjson::pretty)
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn notes(docs: &[Document], stats: &Stats, fallback: Option<&str>) -> Vec<String> {
    let mut notes: Vec<String> = fallback.map(str::to_owned).into_iter().collect();
    if stats.cut > 0 {
        notes.push(format!(
            "{} long value{} shortened (say \"compact\" to see them whole)",
            stats.cut,
            plural(stats.cut)
        ));
    }
    if stats.collapsed > 0 {
        notes.push(format!(
            "{} Slack blocks over the 50-block limit collapsed into a note",
            stats.collapsed
        ));
    }
    let deep: usize = docs.iter().map(|d| d.deep).sum();
    if deep > 0 {
        notes.push(format!(
            "{deep} field{} nested deeper than {MAX_DEPTH} levels shown as JSON",
            plural(deep)
        ));
    }
    notes
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::convert_md::tests_support::SOURCE;
    use crate::fixtures;

    #[test]
    fn same_input_twice_is_byte_identical() {
        for value in [
            fixtures::thread_untangler(),
            fixtures::pre_mortem(),
            fixtures::hostile(),
        ] {
            for target in [Target::Markdown, Target::Slack, Target::Email] {
                let opts = Options {
                    target,
                    ..Options::default()
                };
                assert_eq!(
                    render_values(std::slice::from_ref(&value), &opts).to_envelope(),
                    render_values(std::slice::from_ref(&value), &opts).to_envelope()
                );
            }
        }
    }

    #[test]
    fn sabotaged_slack_renderer_falls_back_to_markdown() {
        let broken = |docs: &[Document], opts: &Options| {
            let mut drawn = draw(docs, opts);
            if opts.target == Target::Slack {
                drawn.visible = "[redacted]".to_owned();
            }
            drawn
        };
        let opts = Options {
            target: Target::Slack,
            ..Options::default()
        };
        let result = render_values_with(&[fixtures::thread_untangler()], &opts, &broken);
        assert_eq!((result.target, result.lang), ("markdown", ""));
        assert_eq!(
            result.notes[0],
            "Could not render as Slack blocks without changing content, so here it is as Markdown."
        );
        assert!(
            result.formatted.contains("Update the pricing page") && result.confidence == "medium"
        );
    }

    #[test]
    fn when_markdown_also_fails_the_json_is_returned_as_given() {
        let broken = |docs: &[Document], opts: &Options| Drawn {
            visible: "?".to_owned(),
            ..draw(docs, opts)
        };
        let value = json!({"rows": [{"a": "alpha"}]});
        let result = render_values_with(std::slice::from_ref(&value), &Options::default(), &broken);
        assert_eq!(result.lang, "json");
        assert_eq!(
            serde_json::from_str::<Value>(&result.formatted).ok(),
            Some(value)
        );
        assert_eq!(result.notes[0], replies::NOT_FAITHFUL_ANY);
    }

    #[test]
    fn markdown_request_renders_as_slack_blocks_and_markdown_stays_unchanged() {
        let result = render_markdown(
            SOURCE,
            &Options {
                target: Target::Slack,
                ..Options::default()
            },
        );
        let payload: Value = serde_json::from_str(&result.formatted).expect("valid JSON");
        assert_eq!((result.target, result.confidence), ("slack", "high"));
        assert_eq!(payload["blocks"][0]["type"], "section");
        assert_eq!(
            render_markdown(SOURCE, &Options::default()).formatted,
            SOURCE.trim()
        );
    }
}
