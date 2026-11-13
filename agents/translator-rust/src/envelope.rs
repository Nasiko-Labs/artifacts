//! Pure: the checked chunk results assembled into the output envelope (aligned items, the restored
//! translation, notes, confidence, needs_human), and an input envelope rebuilt with its fields
//! translated.

use serde_json::{Map, Value, json};

use crate::languages;
use crate::mask;
use crate::register;
use crate::request::Job;
use crate::segments::{self, Segment};
use crate::translate::{ChunkResult, ChunkStatus, Prepared};
use crate::util::collapse_ws;
use crate::verify;

const LEGAL_MEDICAL: &[&str] = &[
    "hereby",
    "liability",
    "indemnif",
    "dosage",
    "contraindicat",
    "diagnos",
    "patient",
    "governing law",
    "warrant",
];
const LEGAL_MIN_HITS: usize = 2;

#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    Ok {
        envelope: Map<String, Value>,
        translated_input: Option<Map<String, Value>>,
    },
    /// The text is already in the target language.
    Already,
    /// No chunk was translated; why.
    Failed(ChunkStatus),
}

/// The most common detected base code, first seen winning ties.
fn detected(ok: &[&ChunkResult]) -> String {
    let mut counts: Vec<(String, usize)> = Vec::new();
    for r in ok.iter().filter(|r| !r.source_language.is_empty()) {
        let base = languages::base(&r.source_language);
        match counts.iter_mut().find(|(c, _)| *c == base) {
            Some(entry) => entry.1 += 1,
            None => counts.push((base, 1)),
        }
    }
    let best = counts.iter().map(|(_, n)| *n).max().unwrap_or(0);
    counts
        .into_iter()
        .find(|(_, n)| *n == best)
        .map_or(String::new(), |(c, _)| c)
}

fn failure(results: &[ChunkResult]) -> ChunkStatus {
    let any = |s| results.iter().any(|r| r.status == s);
    if any(ChunkStatus::Unreachable) {
        ChunkStatus::Unreachable
    } else if any(ChunkStatus::Unusable) {
        ChunkStatus::Unusable
    } else {
        ChunkStatus::Skipped
    }
}

pub fn build(
    job: &Job,
    prepared: &Prepared,
    chunks: &[Vec<usize>],
    results: &[ChunkResult],
) -> Outcome {
    let ok: Vec<&ChunkResult> = results
        .iter()
        .filter(|r| r.status == ChunkStatus::Ok)
        .collect();
    if ok.is_empty() {
        return Outcome::Failed(failure(results));
    }
    let source_code = detected(&ok);
    if ok.len() == results.len() && source_code == languages::base(&job.target) {
        return Outcome::Already;
    }
    let mut texts: Vec<String> = prepared.segments.iter().map(|s| s.text.clone()).collect();
    for (ids, result) in chunks.iter().zip(results) {
        if result.status == ChunkStatus::Ok {
            for (&i, text) in ids.iter().zip(&result.translations) {
                if let Some(slot) = texts.get_mut(i) {
                    *slot = text.clone();
                }
            }
        }
    }
    let restored_src: Vec<String> = prepared
        .segments
        .iter()
        .map(|s| mask::restore(&s.text, &prepared.spans))
        .collect();
    let restored_out: Vec<String> = texts
        .iter()
        .map(|t| mask::restore(t, &prepared.spans))
        .collect();
    let source_all = prepared
        .docs
        .iter()
        .map(|(_, t)| t.as_str())
        .collect::<Vec<_>>()
        .join("\n\n");
    let (notes, level) = notes(
        job,
        results,
        &source_code,
        &source_all,
        &restored_src,
        &restored_out,
    );
    let confidence = ["high", "medium", "low"][level.min(2)];
    let per_field = by_field(prepared, &restored_out);
    let translation = match &job.envelope {
        Some(_) => per_field
            .iter()
            .map(|(_, t)| t.as_str())
            .collect::<Vec<_>>()
            .join("\n\n"),
        None => per_field.first().map_or(String::new(), |(_, t)| t.clone()),
    };
    let translated_input = job.envelope.as_ref().map(|input| {
        let mut out = input.clone();
        for (name, text) in &per_field {
            if let Some(name) = name {
                out.insert(name.clone(), Value::String(text.clone()));
            }
        }
        out
    });
    let items: Vec<Value> = restored_src
        .iter()
        .zip(&restored_out)
        .enumerate()
        .map(|(i, (s, t))| json!({"n": i + 1, "source": s, "translation": t}))
        .collect();
    let envelope = json!({
        "summary": summary(job, &source_code, prepared.segments.len()),
        "items": items,
        "translation": translation,
        "source_language": source_code,
        "target_language": job.target,
        "register": job.register,
        "notes": notes,
        "confidence": confidence,
        "needs_human": confidence == "low" || legal_or_medical(&source_all),
    });
    Outcome::Ok {
        envelope: envelope.as_object().cloned().unwrap_or_default(),
        translated_input,
    }
}

fn by_field(prepared: &Prepared, restored_out: &[String]) -> Vec<(Option<String>, String)> {
    prepared
        .docs
        .iter()
        .map(|(name, _)| {
            let ids: Vec<usize> = (0..prepared.segments.len())
                .filter(|&i| prepared.segments[i].field == *name)
                .collect();
            let texts: Vec<String> = ids.iter().map(|&i| restored_out[i].clone()).collect();
            let segs: Vec<&Segment> = ids.iter().map(|&i| &prepared.segments[i]).collect();
            (name.clone(), segments::join(&texts, &segs))
        })
        .collect()
}

/// (notes, level) where level 0 = high, 1 = medium, 2 = low confidence.
fn notes(
    job: &Job,
    results: &[ChunkResult],
    source_code: &str,
    source_all: &str,
    restored_src: &[String],
    restored_out: &[String],
) -> (Vec<Value>, usize) {
    let mut level = 0;
    let mut code: Vec<String> = Vec::new();
    if !job.target_note.is_empty() {
        code.push(job.target_note.clone());
        level = 1;
    }
    let register_note = register::note(&languages::base(&job.target), job.register);
    if !register_note.is_empty() {
        code.push(register_note);
    }
    let ok = || results.iter().filter(|r| r.status == ChunkStatus::Ok);
    if ok().any(|r| r.repaired || r.structure_fixed || !r.code_notes.is_empty()) {
        level = level.max(1);
    }
    code.extend(results.iter().flat_map(|r| r.code_notes.iter().cloned()));
    if ok().any(|r| r.digits_left) {
        level = 2;
    }
    let missing: Vec<usize> = (1..=results.len())
        .filter(|&n| results[n - 1].status != ChunkStatus::Ok)
        .collect();
    if !missing.is_empty() {
        level = 2;
        let skipped = results.iter().any(|r| r.status == ChunkStatus::Skipped);
        let done = results.len() - missing.len();
        code.push(if skipped {
            format!(
                "Translated the first {done} of {} parts before my time limit; the rest is left in the original.",
                results.len()
            )
        } else {
            let numbers: Vec<String> = missing.iter().map(usize::to_string).collect();
            let plural = if missing.len() > 1 { "s" } else { "" };
            format!(
                "Part{plural} {} could not be translated and are left in the original.",
                numbers.join(", ")
            )
        });
    }
    if let Some(length) = verify::length_note(
        &restored_src.join("\n"),
        &restored_out.join("\n"),
        source_code,
        &job.target,
    ) {
        code.push(length);
        level = level.max(1);
    }
    let joined_out = restored_out.join("\n").to_lowercase();
    let source_lower = source_all.to_lowercase();
    for (term, wanted) in &job.glossary {
        if source_lower.contains(&term.to_lowercase())
            && !joined_out.contains(&wanted.to_lowercase())
        {
            code.push(format!(
                "Glossary: \"{term}\" should be \"{wanted}\", which is not in the translation; check it."
            ));
            level = level.max(1);
        }
    }
    let raw: Vec<Value> = results
        .iter()
        .flat_map(|r| r.notes.iter().cloned())
        .collect();
    let mut all: Vec<Value> = code
        .into_iter()
        .map(|n| json!({"term": "", "note": n}))
        .collect();
    all.extend(verify::model_notes(&raw, source_all));
    (all, level)
}

pub fn legal_or_medical(text: &str) -> bool {
    let lowered = collapse_ws(&text.to_lowercase());
    LEGAL_MEDICAL
        .iter()
        .filter(|w| lowered.contains(*w))
        .count()
        >= LEGAL_MIN_HITS
}

fn summary(job: &Job, source_code: &str, paragraphs: usize) -> String {
    let unit = if paragraphs == 1 {
        "segment"
    } else {
        "segments"
    };
    format!(
        "Translated {paragraphs} {unit} from {} to {} ({}).",
        languages::name_for(source_code),
        languages::name_of(&job.target),
        job.register
    )
}
