//! Pure: library-vs-app classification, collapsing of repeated frames, and the candidate location.
//!
//! The candidate frame is decided here, never by the model: the innermost APP frame of the root-cause
//! block. When the root block has no app frame, the innermost frame overall is the candidate (origin
//! "library") and the nearest app frame outward is named as the call that led there.

use serde_json::Value;

use crate::trace_model::{Block, Frame, Origin, Trace};

const LIBRARY_PATH_MARKERS: &[&str] = &[
    "site-packages/",
    "dist-packages/",
    "node_modules/",
    "/lib/python",
    "<frozen ",
    "/usr/lib/",
    "/usr/local/lib/",
    ".cargo/registry",
    "/rustc/",
    "/go/pkg/mod/",
    "GOROOT",
    "/usr/local/go/src/",
    "/usr/lib/go",
    "vendor/",
    "node:internal",
    "node:",
    "/gems/",
    "<internal:",
];
const LIBRARY_FUNC_PREFIXES: &[&str] = &[
    "java.",
    "javax.",
    "jdk.",
    "sun.",
    "kotlin.",
    "kotlinx.",
    "org.springframework.",
    "org.apache.",
    "com.sun.",
    "com.zaxxer.",
    "org.hibernate.",
    "com.fasterxml.",
    "io.netty.",
    "okhttp3.",
    "com.google.",
    "reactor.",
    "System.",
    "Microsoft.",
    "runtime.",
    "net/http.",
    "sync.",
    "reflect.",
    "syscall.",
    "core::",
    "std::",
    "alloc::",
    "rust_begin_unwind",
    "__rust",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    pub frame: Option<Frame>,
    pub origin: Origin,
    /// Library candidate: the nearest app frame outward.
    pub led_from: Option<Frame>,
}

impl Candidate {
    fn new(frame: Option<Frame>, origin: Origin) -> Self {
        Self {
            frame,
            origin,
            led_from: None,
        }
    }
}

pub fn is_library(frame: &Frame, trust_libraries: bool) -> bool {
    if trust_libraries {
        return false;
    }
    let path = frame.file.replace('\\', "/");
    LIBRARY_PATH_MARKERS.iter().any(|m| path.contains(m))
        || LIBRARY_FUNC_PREFIXES
            .iter()
            .any(|p| frame.func.starts_with(p))
}

/// Origins set on every frame, and consecutive identical frames collapsed into one with `repeat=N`.
pub fn classify(trace: Trace, trust_libraries: bool) -> Trace {
    let blocks = trace
        .blocks
        .iter()
        .map(|b| {
            let frames = b
                .frames
                .iter()
                .map(|f| {
                    let mut f = f.clone();
                    f.origin = if is_library(&f, trust_libraries) {
                        Origin::Library
                    } else {
                        Origin::App
                    };
                    f
                })
                .collect();
            Block::new(&b.exception_type, &b.message, collapse(frames))
        })
        .collect();
    Trace { blocks, ..trace }
}

pub fn collapse(frames: Vec<Frame>) -> Vec<Frame> {
    let mut out: Vec<Frame> = Vec::new();
    for frame in frames {
        match out.last_mut() {
            Some(last) if last.same_key(&frame) => {
                last.repeat = last.repeat.saturating_add(frame.repeat)
            }
            _ => out.push(frame),
        }
    }
    out
}

/// The root block's app frames, innermost first: what the model may choose among by index.
pub fn app_frames(trace: &Trace) -> Vec<&Frame> {
    trace
        .root_block()
        .frames
        .iter()
        .filter(|f| f.origin == Origin::App)
        .collect()
}

pub fn candidate(trace: &Trace) -> Candidate {
    let root_frames = &trace.root_block().frames;
    if let Some(frame) = root_frames.iter().find(|f| f.origin == Origin::App) {
        return Candidate::new(Some(frame.clone()), Origin::App);
    }
    let others: Vec<&Frame> = trace
        .blocks
        .iter()
        .enumerate()
        .filter(|(i, _)| *i != trace.root)
        .flat_map(|(_, b)| b.frames.iter())
        .collect();
    let first_app = others
        .iter()
        .find(|f| f.origin == Origin::App)
        .map(|f| (*f).clone());
    let Some(innermost) = root_frames.first() else {
        return match first_app {
            Some(app) => Candidate::new(Some(app), Origin::App),
            None => match others.first() {
                Some(f) => Candidate::new(Some((*f).clone()), Origin::Library),
                None => Candidate::new(None, Origin::None),
            },
        };
    };
    Candidate {
        frame: Some(innermost.clone()),
        origin: Origin::Library,
        led_from: first_app,
    }
}

/// The model's `frame_index` into the app-frame list, resolved by code: (candidate, moved). An index
/// outside the list (or not an integer) falls back to the code's own candidate.
pub fn pick(trace: &Trace, default: &Candidate, index: &Value) -> (Candidate, bool) {
    let frames = app_frames(trace);
    let Some(i) = index.as_u64().and_then(|i| usize::try_from(i).ok()) else {
        return (default.clone(), false);
    };
    match frames.get(i) {
        Some(frame) => {
            let moved = default.frame.as_ref() != Some(*frame);
            (Candidate::new(Some((*frame).clone()), Origin::App), moved)
        }
        None => (default.clone(), false),
    }
}
