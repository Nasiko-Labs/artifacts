"""Pure: library-vs-app classification, collapsing of repeated frames, and the candidate location.

The candidate frame is decided here, never by the model: the innermost APP frame of the root-cause block. When the
root block has no app frame, the innermost frame overall is the candidate (origin "library") and the nearest app
frame outward is named as the call that led there."""

from __future__ import annotations

from dataclasses import dataclass, replace

from trace_model import Block, Frame, Trace

LIBRARY_PATH_MARKERS = (
    "site-packages/", "dist-packages/", "node_modules/", "/lib/python", "<frozen ", "/usr/lib/", "/usr/local/lib/",
    ".cargo/registry", "/rustc/", "/go/pkg/mod/", "GOROOT", "/usr/local/go/src/", "/usr/lib/go", "vendor/",
    "node:internal", "node:", "/gems/", "<internal:",
)
LIBRARY_FUNC_PREFIXES = (
    "java.", "javax.", "jdk.", "sun.", "kotlin.", "kotlinx.", "org.springframework.", "org.apache.", "com.sun.",
    "com.zaxxer.", "org.hibernate.", "com.fasterxml.", "io.netty.", "okhttp3.", "com.google.", "reactor.",
    "System.", "Microsoft.", "runtime.", "net/http.", "sync.", "reflect.", "syscall.", "core::", "std::", "alloc::",
    "rust_begin_unwind", "__rust",
)


@dataclass(frozen=True)
class Candidate:
    frame: Frame | None
    origin: str                  # app | library | none
    led_from: Frame | None = None  # library candidate: the nearest app frame outward


def is_library(frame: Frame, trust_libraries: bool = False) -> bool:
    if trust_libraries:
        return False
    path = frame.file.replace("\\", "/")
    if any(marker in path for marker in LIBRARY_PATH_MARKERS):
        return True
    return any(frame.func.startswith(prefix) for prefix in LIBRARY_FUNC_PREFIXES)


def classify(trace: Trace, trust_libraries: bool = False) -> Trace:
    """Origins set on every frame, and consecutive identical frames collapsed into one with `repeat=N`."""
    blocks = tuple(Block(b.exception_type, b.message, collapse(tuple(
        replace(f, origin="library" if is_library(f, trust_libraries) else "app") for f in b.frames)))
        for b in trace.blocks)
    return trace.with_blocks(blocks)


def collapse(frames: tuple[Frame, ...]) -> tuple[Frame, ...]:
    out: list[Frame] = []
    for frame in frames:
        if out and out[-1].key() == frame.key():
            out[-1] = replace(out[-1], repeat=out[-1].repeat + frame.repeat)
        else:
            out.append(frame)
    return tuple(out)


def app_frames(trace: Trace) -> list[Frame]:
    """The root block's app frames, innermost first: what the model may choose among by index."""
    return [f for f in trace.root_block.frames if f.origin == "app"]


def candidate(trace: Trace) -> Candidate:
    root_frames = trace.root_block.frames
    for frame in root_frames:
        if frame.origin == "app":
            return Candidate(frame, "app")
    others = [f for i, b in enumerate(trace.blocks) if i != trace.root for f in b.frames]
    if not root_frames:
        app = next((f for f in others if f.origin == "app"), None)
        return Candidate(app, "app") if app else Candidate(others[0] if others else None,
                                                          "library" if others else "none")
    led_from = next((f for f in others if f.origin == "app"), None)
    return Candidate(root_frames[0], "library", led_from)


def pick(trace: Trace, default: Candidate, index: object) -> tuple[Candidate, bool]:
    """The model's `frame_index` into the app-frame list, resolved by code. (candidate, accepted). An index outside
    the list (or not an int) falls back to the code's own candidate."""
    if index is None:
        return default, False
    frames = app_frames(trace)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(frames):
        return default, False
    return Candidate(frames[index], "app"), frames[index] != default.frame


def caller_of(trace: Trace, frame: Frame) -> Frame | None:
    """The app frame that called `frame` (the next app frame outward in the same block), if any."""
    for block in trace.blocks:
        if frame in block.frames:
            after = block.frames[block.frames.index(frame) + 1:]
            return next((f for f in after if f.origin == "app"), None)
    return None
