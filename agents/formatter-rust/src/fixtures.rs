//! Test-only: envelopes shaped like the catalog agents' outputs, used across tests.

use serde_json::{Value, json};

pub fn thread_untangler() -> Value {
    let items = json!([
        {"task": "Update the pricing page", "owner_name": "Lee", "due": "2026-11-14", "quote": "I can update it"},
        {"task": "Send the revised deck | v2", "owner_name": "Sam", "due": null, "quote": "I'll send the deck"}
    ]);
    json!({
        "summary": "Pricing thread: keep the mid tier at $49, drop starter; two open questions.",
        "tldr": ["Mid tier stays at $49 per seat.", "Starter plan is dropped from the page."],
        "decisions": [{"text": "Keep the mid tier at $49 per seat", "quote": "We will keep the mid tier at $49",
                       "message_id": "m3"}],
        "open_questions": [{"text": "Annual discounts?", "asked_by_message": "m6", "answered_in": null}],
        "action_items": items.clone(),
        "reversals": [],
        "items": items,
        "confidence": "medium",
        "needs_human": false
    })
}

pub fn promises() -> Value {
    json!({
        "summary": "3 promises found, 1 overdue.",
        "promises": [
            {"to_whom": "Acme", "text": "Send the SOW", "due": "2026-11-01", "state": "open", "days_overdue": 6},
            {"to_whom": "Beta Co", "text": "Share the roadmap", "due": "2026-11-20", "state": "open", "days_overdue": 0}
        ],
        "overdue": ["Send the SOW to Acme (6 days)"],
        "confidence": "high",
        "needs_human": true
    })
}

pub fn ranked() -> Value {
    json!({
        "summary": "5 tasks ranked.",
        "ranked": [{"task": "Fix login bug", "reason": "Blocks 3 customers", "score": 9.5, "rank": 1},
                   {"task": "Write Q4 plan", "reason": "Due Friday", "score": 7, "rank": 2}],
        "drop_or_delegate": ["Reorganise the wiki"]
    })
}

pub fn pre_mortem() -> Value {
    json!({
        "summary": "4 ways the launch could fail.",
        "failure_modes": [
            {"title": "Payments outage at peak", "likelihood": "medium", "impact": "high",
             "signals": ["error rate > 2%", "checkout latency"], "mitigation": {"owner": "Ops", "step": "load test"}},
            {"title": "Docs not ready", "likelihood": "high", "impact": "medium", "signals": [], "mitigation": null}
        ],
        "unstated_assumptions": ["The vendor ships on time"],
        "questions_for_the_team": ["Who owns rollback?"],
        "confidence": "medium",
        "needs_human": false
    })
}

pub fn draft() -> Value {
    json!({"summary": "Reply drafted.", "draft": "Hi Ana,\nThanks for the note.\nBest, Sam",
           "alt_shorter": "Thanks Ana!", "tone_notes": ["Warm", "Short"]})
}

pub fn memo() -> Value {
    json!({"summary": "Recommend option B.", "memo_markdown": "## Context\nWe need **one** vendor.\n\n- B is cheaper",
           "recommendation": "Option B", "reversal_triggers": ["Price rises > 20%"], "scores": {"A": 6, "B": 8}})
}

pub fn generic() -> Value {
    json!({"a": 1})
}

pub fn hostile() -> Value {
    json!({
        "summary": "Ping <!channel> & <@U123> <https://evil.example|click> *bold* _it_",
        "items": [{"name": "x<script>alert(1)</script>", "link": "javascript:alert(1)",
                   "home": "https://example.com/a_b?x=1&y=2", "mail": "mailto:a@example.com"}]
    })
}

pub fn all() -> Vec<Value> {
    vec![
        thread_untangler(),
        promises(),
        ranked(),
        pre_mortem(),
        draft(),
        memo(),
        generic(),
        hostile(),
        json!({"quote": "She said \"ship it\" \\ now", "unicode": "café ✓", "n": [1, 2.5, -3]}),
    ]
}
