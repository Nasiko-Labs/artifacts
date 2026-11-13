//! Test-only: deterministic test texts and envelopes shaped like the catalog agents' outputs.

use serde_json::{Value, json};

const SPEAKERS: [&str; 5] = ["Priya", "Sam", "Lee", "Dana Ortiz", "Mo"];
const WORDS: [&str; 20] = [
    "pricing",
    "launch",
    "deck",
    "customer",
    "renewal",
    "budget",
    "roadmap",
    "hiring",
    "migration",
    "review",
    "contract",
    "timeline",
    "support",
    "metrics",
    "onboarding",
    "release",
    "Acme",
    "vendor",
    "risk",
    "plan",
];

/// A small deterministic generator (xorshift64*), so test texts are the same on every run.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }

    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// A number in `0..n`.
    pub fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }

    fn between(&mut self, lo: usize, hi: usize) -> usize {
        lo + self.below(hi - lo + 1)
    }

    fn pick<'a>(&mut self, items: &[&'a str]) -> &'a str {
        items[self.below(items.len())]
    }
}

fn sentence(rng: &mut Rng) -> String {
    let count = rng.between(5, 14);
    let mut words: Vec<String> = (0..count).map(|_| rng.pick(&WORDS).to_owned()).collect();
    let mut first = words[0].chars();
    words[0] = first
        .next()
        .map(|c| c.to_uppercase().chain(first).collect())
        .unwrap_or_default();
    format!("{}{}", words.join(" "), rng.pick(&[".", ".", "?", "!"]))
}

fn sentences(rng: &mut Rng, lo: usize, hi: usize) -> String {
    let count = rng.between(lo, hi);
    (0..count)
        .map(|_| sentence(rng))
        .collect::<Vec<_>>()
        .join(" ")
}

pub fn transcript(chars: usize, seed: u64) -> String {
    let mut rng = Rng::new(seed);
    let (mut lines, mut total) = (Vec::new(), 0);
    while total < chars {
        let line = format!("{}: {}", rng.pick(&SPEAKERS), sentences(&mut rng, 1, 4));
        total += line.chars().count() + 1;
        lines.push(line);
    }
    lines.join("\n")
}

pub fn document(sections: usize, paragraphs: usize, seed: u64) -> String {
    let mut rng = Rng::new(seed);
    let mut out = vec!["# Quarterly plan".to_owned()];
    for s in 1..=sections {
        out.push(format!("## Section {s}"));
        for _ in 0..paragraphs {
            out.push(sentences(&mut rng, 3, 8));
        }
    }
    out.join("\n\n")
}

pub fn prose(chars: usize, seed: u64) -> String {
    let mut rng = Rng::new(seed);
    let (mut paragraphs, mut total) = (Vec::new(), 0);
    while total < chars {
        let p = sentences(&mut rng, 2, 9);
        total += p.chars().count() + 2;
        paragraphs.push(p);
    }
    paragraphs.join("\n\n")
}

pub fn summarizer_part(n: usize, confidence: &str, needs_human: bool) -> Value {
    let shared = json!({"task": "Send the revised deck to sales", "owner": "Sam", "due": "Thursday",
                        "quote": "I'll send the revised deck to the sales team"});
    let actions = json!([{"task": format!("Action from part {n}"), "owner": "Lee", "due": null,
                          "quote": format!("Quote unique to part {n}")}, shared]);
    json!({
        "summary": format!("Part {n} of the pricing call."),
        "tldr": format!("In part {n} the team discussed pricing."),
        "decisions": [{"text": "Keep the mid tier at $49", "quote": "We will keep the mid tier at $49"},
                      {"text": format!("Decision {n}"), "quote": format!("Decision quote {n}")}],
        "action_items": actions.clone(),
        "items": actions,
        "words": 1000 * n,
        "confidence": confidence,
        "needs_human": needs_human
    })
}

pub fn part(n: usize) -> Value {
    summarizer_part(n, "high", false)
}

pub fn thread_untangler() -> Value {
    let items = json!([{"task": "Update the pricing page", "owner_name": "Lee", "due": "2026-11-14", "quote": "I can update it"}]);
    json!({
        "summary": "Pricing thread: keep the mid tier at $49, drop starter; two open questions.",
        "tldr": ["Mid tier stays at $49 per seat.", "Starter plan is dropped from the page."],
        "decisions": [{"text": "Keep the mid tier at $49 per seat", "quote": "We will keep the mid tier at $49", "message_id": "m3"}],
        "open_questions": [{"text": "Annual discounts?", "asked_by_message": "m6", "answered_in": null}],
        "action_items": items.clone(), "reversals": [], "items": items,
        "confidence": "medium", "needs_human": false
    })
}

pub fn thread_untangler_2() -> Value {
    let items = json!([{"task": "Review the Acme SOW", "owner_name": null, "due": null, "quote": "the SOW still needs a review"}]);
    json!({
        "summary": "Second half: legal review of the SOW is open.",
        "tldr": ["Starter plan is dropped from the page.", "SOW review has no owner."],
        "decisions": [],
        "open_questions": [{"text": "Who reviews the SOW?", "asked_by_message": "m9", "answered_in": null}],
        "action_items": items.clone(), "reversals": [], "items": items,
        "confidence": "high", "needs_human": true
    })
}

pub fn ranked_1() -> Value {
    json!({"summary": "3 tasks ranked.", "ranked": [{"task": "Fix login", "score": 9, "rank": 1}, {"task": "Write plan", "score": 7, "rank": 2}],
           "confidence": "high", "needs_human": false})
}

pub fn ranked_2() -> Value {
    json!({"summary": "2 tasks ranked.", "ranked": [{"task": "Hire SRE", "score": 8, "rank": 1}],
           "confidence": "high", "needs_human": false})
}
