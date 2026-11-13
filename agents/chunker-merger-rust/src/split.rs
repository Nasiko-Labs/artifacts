//! Pure: one split, end to end. Resolve the size and strategy, cut, verify losslessly (falling back to a
//! plain paragraph split with a note if the check fails), then build the chunk prompts and the envelope.

use serde_json::{Value, json};

use crate::prompts_out::{MERGE_INSTRUCTION, chunk_prompt, strip_markers};
use crate::replies::thousands;
use crate::splitters::{self, STRATEGIES, Span, round_half_even};
use crate::textidx::Text;
use crate::verify::{self, MAX_PARTS};

const DEFAULT_SIZE: usize = 20_000;
const MIN_SIZE: usize = 2_000;
const MAX_SIZE: usize = 60_000;
const CHARS_PER_TOKEN: usize = 4;
/// Widen the size by 1/10 while boundary packing overshoots the part cap.
const GROWTH_STEP: usize = 10;

/// How to cut. `None` (or 0) means "not given", like the original's falsy values.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SplitOptions {
    pub size: Option<usize>,
    pub parts: Option<usize>,
    /// None: auto.
    pub strategy: Option<String>,
    pub overlap_units: usize,
    pub overlap_chars: usize,
    pub step: String,
}

impl SplitOptions {
    /// These options with every field the newer request sets (a size replaces a part count).
    pub fn overlay(&self, newer: &SplitOptions) -> SplitOptions {
        let mut out = SplitOptions {
            size: newer.size.or(self.size),
            parts: newer.parts.or(self.parts),
            strategy: newer.strategy.clone().or_else(|| self.strategy.clone()),
            overlap_units: if newer.overlap_units > 0 {
                newer.overlap_units
            } else {
                self.overlap_units
            },
            overlap_chars: if newer.overlap_chars > 0 {
                newer.overlap_chars
            } else {
                self.overlap_chars
            },
            step: if newer.step.is_empty() {
                self.step.clone()
            } else {
                newer.step.clone()
            },
        };
        if newer.size.is_some() {
            out.parts = None;
        }
        if newer.parts.is_some() {
            out.size = None;
        }
        out
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Part {
    pub n: usize,
    pub of: usize,
    pub chars: usize,
    pub est_tokens: usize,
    pub label: String,
    pub split_mid_unit: bool,
    pub overlap_chars: usize,
    pub prompt: String,
}

impl Part {
    fn to_value(&self) -> Value {
        json!({"n": self.n, "of": self.of, "chars": self.chars, "est_tokens": self.est_tokens, "label": self.label,
               "split_mid_unit": self.split_mid_unit, "overlap_chars": self.overlap_chars, "prompt": self.prompt})
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SplitResult {
    pub parts: Vec<Part>,
    pub strategy: String,
    pub chars: usize,
    pub confidence: &'static str,
    pub notes: Vec<String>,
}

impl SplitResult {
    pub fn summary(&self) -> String {
        let n = self.parts.len();
        if n == 1 {
            return format!(
                "{} chars fit in one part (by {}).",
                thousands(self.chars as u64),
                self.strategy
            );
        }
        let each = round_half_even(self.chars as f64 / n as f64).max(0) as u64;
        format!(
            "Split {} chars into {n} parts (by {}, ~{} chars each).",
            thousands(self.chars as u64),
            self.strategy,
            thousands(each)
        )
    }

    pub fn envelope(&self) -> Value {
        let items: Vec<Value> = self.parts.iter().map(Part::to_value).collect();
        json!({"summary": self.summary(), "items": items.clone(), "chunks": items,
               "merge_instruction": MERGE_INSTRUCTION, "strategy": self.strategy, "notes": self.notes,
               "confidence": self.confidence, "needs_human": false})
    }
}

/// How spans are computed; tests swap in a broken one to prove the fallback.
pub type SpansFn = dyn Fn(&Text, &[usize], &SplitOptions) -> (Vec<Span>, Vec<String>);

pub fn split_text(text: &str, opts: &SplitOptions) -> Result<SplitResult, String> {
    split_text_with(text, opts, &spans)
}

pub fn split_text_with(
    text: &str,
    opts: &SplitOptions,
    spans_fn: &SpansFn,
) -> Result<SplitResult, String> {
    let text = Text::new(text);
    let (mut strategy, mut notes) = choose_strategy(&text, opts.strategy.as_deref());
    let mut starts = splitters::unit_starts(&text, strategy);
    let (mut spans, size_notes) = spans_fn(&text, &starts, opts);
    notes.extend(size_notes);
    let bad = verify::problem(&text, &spans);
    if let Some(bad) = &bad {
        notes.push(format!(
            "The {strategy} split failed its check ({bad}); fell back to a plain paragraph split."
        ));
        strategy = "paragraphs";
        starts = splitters::unit_starts(&text, strategy);
        spans = fallback_spans(&text, &starts);
        if verify::problem(&text, &spans).is_some() {
            return Err("the fallback split failed its lossless check".to_owned());
        }
    }
    let (parts, stripped) = build_parts(&text, &spans, &starts, strategy, &opts.step);
    if stripped > 0 {
        notes.push(format!(
            "Removed {stripped} fence marker(s) from the text so each part's fence stays intact."
        ));
    }
    let mid = spans.iter().any(|s| s.mid);
    if mid {
        notes.push(
            "Some units were longer than a part, so they were cut at a sentence or line break (flagged split_mid_unit)."
                .to_owned(),
        );
    }
    let structural = matches!(strategy, "turns" | "headings");
    let confidence = if structural && !mid && bad.is_none() {
        "high"
    } else {
        "medium"
    };
    Ok(SplitResult {
        parts,
        strategy: strategy.to_owned(),
        chars: text.len(),
        confidence,
        notes,
    })
}

pub fn choose_strategy(text: &Text, requested: Option<&str>) -> (&'static str, Vec<String>) {
    let detected = splitters::detect(text);
    let Some(requested) = requested.and_then(|r| STRATEGIES.iter().find(|s| **s == r).copied())
    else {
        return (detected, Vec::new());
    };
    if requested == "sentences" || splitters::finder(requested, text).len() >= 2 {
        return (requested, Vec::new());
    }
    let note = format!(
        "I found no {} to split by, so I split by {detected}.",
        splitters::unit_words(requested)
    );
    (detected, vec![note])
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sizing {
    Parts(usize),
    Size(usize),
}

fn ceil_div(a: usize, b: usize) -> usize {
    a.div_ceil(b.max(1))
}

/// Parts(n) or Size(chars), with notes on any clamp.
pub fn resolve_size(length: usize, opts: &SplitOptions) -> (Sizing, Vec<String>) {
    let mut notes = Vec::new();
    if let Some(asked) = opts.parts {
        let mut n = asked.clamp(1, MAX_PARTS);
        if n != asked {
            notes.push(format!("I split into at most {MAX_PARTS} parts."));
        }
        if length as f64 / n as f64 > MAX_SIZE as f64 {
            n = ceil_div(length, MAX_SIZE);
            notes.push(format!(
                "{asked} parts would be over {} chars each, so I made {n}.",
                thousands(MAX_SIZE as u64)
            ));
        } else if n > 1 && (length as f64 / n as f64) < MIN_SIZE as f64 {
            n = std::cmp::max(1, length / MIN_SIZE);
            notes.push(format!(
                "Parts under {} chars aren't useful, so I made {n}.",
                thousands(MIN_SIZE as u64)
            ));
        }
        return (Sizing::Parts(n), notes);
    }
    let size = opts.size.unwrap_or(DEFAULT_SIZE);
    let mut clamped = size.clamp(MIN_SIZE, MAX_SIZE);
    if clamped != size {
        notes.push(format!(
            "Part size is kept between {} and {} chars, so I used {}.",
            thousands(MIN_SIZE as u64),
            thousands(MAX_SIZE as u64),
            thousands(clamped as u64)
        ));
    }
    if ceil_div(length, clamped) > MAX_PARTS {
        clamped = ceil_div(length, MAX_PARTS);
        notes.push(format!(
            "That would be over {MAX_PARTS} parts, so each part is up to {} chars.",
            thousands(clamped as u64)
        ));
    }
    (Sizing::Size(clamped), notes)
}

fn spans(text: &Text, starts: &[usize], opts: &SplitOptions) -> (Vec<Span>, Vec<String>) {
    let (sizing, notes) = resolve_size(text.len(), opts);
    let (cuts, mids) = match sizing {
        Sizing::Parts(n) => splitters::pack_into(text, starts, n),
        Sizing::Size(mut value) => {
            let mut packed = splitters::pack_by_size(text, starts, value);
            while packed.0.len() + 1 > MAX_PARTS {
                value += std::cmp::max(1, value / GROWTH_STEP);
                packed = splitters::pack_by_size(text, starts, value);
            }
            packed
        }
    };
    let spans = splitters::to_spans(text.len(), &cuts, &mids);
    (
        splitters::with_overlap(text, &spans, starts, opts.overlap_units, opts.overlap_chars),
        notes,
    )
}

fn fallback_spans(text: &Text, starts: &[usize]) -> Vec<Span> {
    let size = std::cmp::max(DEFAULT_SIZE, ceil_div(text.len(), MAX_PARTS));
    let (cuts, mids) = splitters::pack_by_size(text, starts, size);
    splitters::to_spans(text.len(), &cuts, &mids)
}

fn build_parts(
    text: &Text,
    spans: &[Span],
    starts: &[usize],
    strategy: &str,
    step: &str,
) -> (Vec<Part>, usize) {
    let total = spans.len();
    let mut stripped = 0;
    let parts = spans
        .iter()
        .enumerate()
        .map(|(i, span)| {
            let n = i + 1;
            let (body, removed) = strip_markers(text.slice(span.view_start, span.end).trim());
            stripped += removed;
            let label = splitters::unit_label(starts, strategy, span);
            let overlap = text
                .slice(span.view_start, span.start)
                .trim_start()
                .chars()
                .count();
            let chars = body.chars().count();
            Part {
                n,
                of: total,
                chars,
                est_tokens: chars.div_ceil(CHARS_PER_TOKEN),
                prompt: chunk_prompt(n, total, &label, step, &body, overlap),
                label,
                split_mid_unit: span.mid,
                overlap_chars: overlap,
            }
        })
        .collect();
    (parts, stripped)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixtures;
    use crate::prompts_out::DEFAULT_STEP;

    fn opts_parts(n: usize) -> SplitOptions {
        SplitOptions {
            parts: Some(n),
            ..SplitOptions::default()
        }
    }

    fn compute(text: &Text, opts: &SplitOptions) -> (&'static str, Vec<usize>, Vec<Span>) {
        let (strategy, _) = choose_strategy(text, opts.strategy.as_deref());
        let starts = splitters::unit_starts(text, strategy);
        let (found, _) = spans(text, &starts, opts);
        (strategy, starts, found)
    }

    fn cores(text: &Text, spans: &[Span]) -> String {
        spans.iter().map(|s| text.slice(s.start, s.end)).collect()
    }

    #[test]
    fn auto_picks_turns_and_packs_whole_turns() {
        let raw = fixtures::transcript(70_000, 7);
        let text = Text::new(&raw);
        let (strategy, starts, spans) = compute(&text, &opts_parts(4));
        assert_eq!((strategy, spans.len()), ("turns", 4));
        assert!(spans.iter().all(|s| starts.contains(&s.start) && !s.mid));
        assert_eq!(cores(&text, &spans), raw);
    }

    #[test]
    fn n_parts_stay_within_125_percent_of_the_even_size() {
        for seed in 0..5 {
            let raw = fixtures::transcript(50_000 + seed * 7_000, seed as u64);
            let text = Text::new(&raw);
            for n in [2, 3, 4, 7] {
                let (_, _, spans) = compute(&text, &opts_parts(n));
                let target = text.len().div_ceil(n) as f64;
                assert_eq!(spans.len(), n);
                assert!(
                    spans
                        .iter()
                        .all(|s| (s.end - s.start) as f64 <= target * 1.25)
                );
            }
        }
    }

    #[test]
    fn size_mode_keeps_each_part_within_the_size() {
        let raw = fixtures::transcript(70_000, 7);
        let text = Text::new(&raw);
        let (_, _, spans) = compute(
            &text,
            &SplitOptions {
                size: Some(8_000),
                ..SplitOptions::default()
            },
        );
        assert!(spans.iter().all(|s| s.end - s.start <= 8_000));
        assert_eq!(cores(&text, &spans), raw);
    }

    #[test]
    fn oversized_turn_is_cut_at_a_sentence_and_flagged() {
        let long_turn: Vec<String> = (0..800)
            .map(|i| format!("Sentence number {i} is here."))
            .collect();
        let text = format!(
            "Sam: short.\nPriya: {}\nLee: ok.\nMo: yes.\nSam: done.\nPriya: bye.",
            long_turn.join(" ")
        );
        let opts = SplitOptions {
            size: Some(5_000),
            strategy: Some("turns".into()),
            ..SplitOptions::default()
        };
        let result = split_text(&text, &opts).expect("a split");
        assert!(result.parts.iter().any(|p| p.split_mid_unit) && result.confidence == "medium");
        for part in &result.parts[..result.parts.len() - 1] {
            let body = part
                .prompt
                .split("<<<TEXT>>>\n")
                .nth(1)
                .and_then(|b| b.split("\n>>>END_TEXT<<<").next())
                .unwrap_or_default();
            assert!(
                body.trim_end().ends_with('.'),
                "{}",
                &body[body.len().saturating_sub(40)..]
            );
        }
    }

    #[test]
    fn overlap_of_two_turns_repeats_the_previous_turns() {
        let raw = fixtures::transcript(30_000, 7);
        let text = Text::new(&raw);
        let (_, starts, spans) = compute(
            &text,
            &SplitOptions {
                parts: Some(3),
                overlap_units: 2,
                ..SplitOptions::default()
            },
        );
        let index = |at: usize| starts.iter().position(|&s| s == at).expect("a unit start");
        for span in &spans[1..] {
            assert!(span.view_start < span.start);
            assert_eq!(index(span.start) - index(span.view_start), 2);
        }
        assert_eq!(cores(&text, &spans), raw);
    }

    #[test]
    fn overlap_in_chars_starts_on_a_word() {
        let raw = fixtures::prose(30_000, 11);
        let text = Text::new(&raw);
        let (_, _, spans) = compute(
            &text,
            &SplitOptions {
                parts: Some(3),
                overlap_chars: 400,
                ..SplitOptions::default()
            },
        );
        for span in &spans[1..] {
            assert!(span.start - span.view_start > 0 && span.start - span.view_start <= 400);
            assert!(
                text.slice(span.view_start - 1, span.view_start)
                    .chars()
                    .all(char::is_whitespace)
            );
        }
    }

    #[test]
    fn heading_splitter_cuts_before_the_shallowest_heading() {
        let raw = fixtures::document(12, 4, 3);
        let text = Text::new(&raw);
        let (strategy, _, spans) = compute(&text, &opts_parts(3));
        assert_eq!(strategy, "headings");
        assert!(
            spans[1..]
                .iter()
                .all(|s| text.slice(s.start, text.len()).starts_with("## Section"))
        );
    }

    #[test]
    fn paragraph_and_sentence_detection() {
        let raw = fixtures::prose(30_000, 11);
        assert_eq!(splitters::detect(&Text::new(&raw)), "paragraphs");
        assert_eq!(
            splitters::detect(&Text::new(&raw.replace("\n\n", " "))),
            "sentences"
        );
    }

    #[test]
    fn requested_strategy_without_units_falls_back_with_a_note() {
        let opts = SplitOptions {
            parts: Some(2),
            strategy: Some("turns".into()),
            ..SplitOptions::default()
        };
        let result = split_text(&fixtures::prose(10_000, 11), &opts).expect("a split");
        assert!(result.strategy == "paragraphs" && result.notes[0].contains("no turns"));
    }

    #[test]
    fn lossless_on_random_texts() {
        for seed in 0..25u64 {
            let mut rng = fixtures::Rng::new(seed);
            let len = 5_000 + rng.below(85_000);
            let raw = if rng.below(2) == 0 {
                fixtures::transcript(len, seed)
            } else {
                fixtures::prose(len, seed)
            };
            let opts = match rng.below(3) {
                0 => opts_parts(1 + rng.below(9)),
                1 => SplitOptions {
                    size: Some(2_000 + rng.below(28_000)),
                    ..SplitOptions::default()
                },
                _ => SplitOptions {
                    size: Some(3_000),
                    strategy: Some("sentences".into()),
                    ..SplitOptions::default()
                },
            };
            let text = Text::new(&raw);
            let (_, _, spans) = compute(&text, &opts);
            assert_eq!(verify::problem(&text, &spans), None, "{seed} {opts:?}");
        }
    }

    #[test]
    fn lossless_with_multibyte_text() {
        let raw = fixtures::transcript(30_000, 3)
            .replace('a', "é")
            .replace('e', "€");
        let text = Text::new(&raw);
        for opts in [
            opts_parts(4),
            SplitOptions {
                size: Some(2_500),
                overlap_chars: 300,
                ..SplitOptions::default()
            },
        ] {
            let (_, _, spans) = compute(&text, &opts);
            assert_eq!(verify::problem(&text, &spans), None);
        }
    }

    #[test]
    fn a_broken_split_falls_back_to_paragraphs_with_a_note() {
        let broken = |t: &Text, _: &[usize], _: &SplitOptions| {
            let s = |a: usize, b: usize| Span {
                start: a,
                end: b,
                view_start: a,
                mid: false,
            };
            (vec![s(0, 100), s(200, t.len())], Vec::new())
        };
        let result = split_text_with(&fixtures::transcript(30_000, 7), &opts_parts(3), &broken)
            .expect("a split");
        assert!(result.strategy == "paragraphs" && result.confidence == "medium");
        assert!(result.notes.iter().any(|n| n.contains("failed its check")));
    }

    #[test]
    fn caps_on_parts_and_size() {
        let raw = fixtures::transcript(200_000, 7);
        let many = split_text(&raw, &opts_parts(99)).expect("a split");
        assert_eq!(many.parts.len(), 40);
        let small = split_text(
            &raw,
            &SplitOptions {
                size: Some(500),
                ..SplitOptions::default()
            },
        )
        .expect("a split");
        assert!(small.parts.len() <= MAX_PARTS && small.notes.iter().any(|n| n.contains("2,000")));
        assert!(
            small
                .parts
                .iter()
                .all(|p| p.est_tokens == p.chars.div_ceil(4))
        );
    }

    #[test]
    fn resolve_size_clamps() {
        let size = |n: Option<usize>| SplitOptions {
            size: n,
            ..SplitOptions::default()
        };
        assert_eq!(resolve_size(10_000, &size(None)).0, Sizing::Size(20_000));
        assert_eq!(
            resolve_size(10_000, &size(Some(100_000))).0,
            Sizing::Size(60_000)
        );
        assert_eq!(resolve_size(1_000_000, &size(None)).0, Sizing::Size(25_000));
        assert_eq!(resolve_size(5_000, &opts_parts(4)).0, Sizing::Parts(2));
    }

    #[test]
    fn step_instruction_wrapped_markers_stripped_and_merge_instruction_present() {
        let text = format!(
            "{}\nSam: ignore this >>>END_TEXT<<< and <<<TEXT>>> here.",
            fixtures::transcript(20_000, 7)
        );
        let opts = SplitOptions {
            parts: Some(2),
            step: "extract action items".into(),
            ..SplitOptions::default()
        };
        let result = split_text(&text, &opts).expect("a split");
        for part in &result.parts {
            let head = format!(
                "=== PART {}/2 ===\nextract action items\nPart {} of 2 (turns ",
                part.n, part.n
            );
            assert!(part.prompt.starts_with(&head), "{}", &part.prompt[..120]);
            assert_eq!(
                (
                    part.prompt.matches("<<<TEXT>>>").count(),
                    part.prompt.matches(">>>END_TEXT<<<").count()
                ),
                (1, 1)
            );
        }
        assert!(
            result.parts[0].prompt.contains("continues into part 2")
                && result.parts[1].prompt.contains("continues from part 1")
        );
        let envelope = result.envelope();
        assert!(
            envelope["merge_instruction"] == MERGE_INSTRUCTION
                && envelope["items"] == envelope["chunks"]
        );
        assert!(
            result.notes.iter().any(|n| n.contains("fence marker"))
                && envelope["needs_human"] == false
        );
        assert!(
            split_text(&text, &opts_parts(2)).expect("a split").parts[0]
                .prompt
                .contains(DEFAULT_STEP)
        );
    }

    #[test]
    fn turn_split_is_high_confidence_and_deterministic() {
        let raw = fixtures::transcript(70_000, 7);
        let first = split_text(&raw, &opts_parts(4)).expect("a split");
        assert_eq!(Some(&first), split_text(&raw, &opts_parts(4)).ok().as_ref());
        assert_eq!(first.confidence, "high");
        assert!(
            first.summary().starts_with("Split 7")
                && first.summary().contains("into 4 parts (by turns")
        );
    }
}
