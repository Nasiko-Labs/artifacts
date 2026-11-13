//! Pure: follow-ups on the last split or merge, with zero calls. Nothing is stored: what they work on is
//! rebuilt from the platform's history slice (recall.rs) and a split is recomputed from its source and
//! options (deterministic), so the parts are identical to the ones shown before.

use serde_json::Value;

use crate::actions::{self, Out};
use crate::classify::{Kind, Request};
use crate::envelope::{extract_values, fenced};
use crate::merge::{MergeResult, Status, merge};
use crate::recall::{Last, Recalled};
use crate::render;
use crate::replies;
use crate::split::{SplitResult, split_text};

pub fn handle(request: &Request, found: &Recalled, has_history: bool) -> Result<Out, String> {
    let kind = request.kind;
    if matches!(kind, Kind::MergeMore | Kind::MergeNotes)
        || (kind == Kind::AsJson && found.last == Last::Merge)
    {
        return Ok(merge_followup(
            request,
            found,
            found.merge_lost || has_history,
        ));
    }
    let Some(last) = last_split(found)? else {
        let lost = found.split_lost || has_history;
        return Ok(Out::reply(if lost {
            replies::SPLIT_NOT_IN_HISTORY
        } else {
            replies::NO_SPLIT_YET
        }));
    };
    match kind {
        Kind::ShowPart => {
            let part = usize::try_from(request.number)
                .ok()
                .and_then(|n| n.checked_sub(1))
                .and_then(|i| last.parts.get(i));
            Ok(match part {
                Some(part) => Out::reply(part.prompt.clone()),
                None => Out::reply(replies::no_such_part(request.number, last.parts.len())),
            })
        }
        Kind::ListParts => Ok(Out::reply(render::part_list(&last))),
        Kind::AsJson => {
            let envelope = last.envelope();
            Ok(Out {
                reply: fenced(&envelope),
                envelope: Some(envelope),
            })
        }
        _ => actions::split(&found.source, &found.split_opts.overlay(&request.opts), &[]),
    }
}

fn last_split(found: &Recalled) -> Result<Option<SplitResult>, String> {
    if found.source.is_empty() {
        return Ok(None);
    }
    split_text(&found.source, &found.split_opts).map(Some)
}

fn last_merge(found: &Recalled) -> Option<MergeResult> {
    if found.merge_set.len() < 2 {
        return None;
    }
    Some(merge(&found.merge_set, &[])).filter(|r| r.status == Status::Ok)
}

fn merge_followup(request: &Request, found: &Recalled, lost: bool) -> Out {
    if request.kind == Kind::MergeMore {
        let pasted = extract_values(&request.payload);
        if pasted.values.is_empty() {
            return Out::reply(replies::NEED_ENVELOPES);
        }
        if found.merge_set.is_empty() && pasted.values.len() < 2 && lost {
            return Out::reply(replies::MERGE_NOT_IN_HISTORY);
        }
        return actions::merge_values(&pasted, &found.merge_set);
    }
    let Some(result) = last_merge(found) else {
        return Out::reply(if lost {
            replies::MERGE_NOT_IN_HISTORY
        } else {
            replies::NO_MERGE_YET
        });
    };
    if request.kind == Kind::MergeNotes {
        return Out::reply(render::merge_notes(&result));
    }
    let envelope: Value = result.envelope.unwrap_or(Value::Null);
    Out {
        reply: fenced(&envelope),
        envelope: Some(envelope),
    }
}
