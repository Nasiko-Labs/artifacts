"""Pure: read changed files out of whatever the GitHub tools returned, and pack them into a token
budget (the idea of PR-Agent's "PR compression", re-implemented here).

Extraction is shape-based, not tool-based: any JSON object in a tool result that carries a
`filename` plus a `patch`/`status`/`additions` field is a changed file (the GitHub REST shape,
however a connector wraps it), and any `diff --git` text is a unified diff.

Compression, in order:
1. Drop what a reviewer cannot use: lockfiles, generated/minified files, binaries and files with
   no patch (listed as skipped, with the reason). Deleted files are listed by name only.
2. Rank the rest: files in the PR's main languages first (languages ranked by changed lines),
   then larger diffs first within a language; docs/config come last.
3. Number every new-side line, so findings can cite real line numbers.
4. If everything fits the soft budget, send it all. Otherwise drop deletion-only hunks, cap any
   one file at a per-file budget (cut at a line boundary, with a note), and add files in rank
   order until the soft budget; a file that would cross the hard budget is left out.
5. List every file that was left out, so the model (and the reader) knows what it did not see.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

CHARS_PER_TOKEN = 4  # rough, provider-neutral; no tokenizer dependency
DEFAULT_DIFF_TOKEN_BUDGET = 24_000
BUDGET_ENV = "PR_AGENT_DIFF_TOKEN_BUDGET"
HARD_BUDGET_MARGIN = 0.05  # PR-Agent keeps a soft and a slightly larger hard threshold
PER_FILE_SHARE = 0.25  # in compressed mode no single file may take more than this share
MIN_PER_FILE_TOKENS = 1_500

_LOCKFILES = frozenset({
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "cargo.lock",
    "poetry.lock", "pipfile.lock", "uv.lock", "pdm.lock", "go.sum", "gemfile.lock", "composer.lock",
    "podfile.lock", "packages.lock.json", "flake.lock", "mix.lock", "pubspec.lock",
})
_BINARY_EXTENSIONS = frozenset({
    "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "svgz", "pdf", "zip", "gz", "bz2", "xz", "tar", "tgz",
    "7z", "jar", "war", "class", "exe", "dll", "so", "dylib", "o", "a", "bin", "pyc", "pyo", "pyd", "whl",
    "egg", "mp3", "mp4", "mov", "avi", "ogg", "wav", "woff", "woff2", "ttf", "otf", "eot", "pkl", "pickle",
    "db", "sqlite", "parquet", "npy", "lockb",
})
_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pb.go", "_pb2.py", "_pb2_grpc.py", ".snap", ".lock")
_GENERATED_DIRS = ("node_modules/", "vendor/", "dist/", "build/", "__generated__/", ".next/")

_LANGUAGES: Mapping[str, str] = {
    "py": "Python", "pyi": "Python", "js": "JavaScript", "jsx": "JavaScript", "mjs": "JavaScript",
    "cjs": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript", "go": "Go", "rs": "Rust", "java": "Java",
    "kt": "Kotlin", "kts": "Kotlin", "scala": "Scala", "rb": "Ruby", "php": "PHP", "cs": "C#", "c": "C",
    "h": "C", "cc": "C++", "cpp": "C++", "cxx": "C++", "hpp": "C++", "swift": "Swift", "m": "Objective-C",
    "dart": "Dart", "ex": "Elixir", "exs": "Elixir", "erl": "Erlang", "hs": "Haskell", "clj": "Clojure",
    "lua": "Lua", "r": "R", "jl": "Julia", "sh": "Shell", "bash": "Shell", "zsh": "Shell", "ps1": "PowerShell",
    "sql": "SQL", "vue": "Vue", "svelte": "Svelte", "html": "HTML", "css": "CSS", "scss": "CSS", "sass": "CSS",
    "less": "CSS", "tf": "HCL", "hcl": "HCL", "proto": "Protobuf", "sol": "Solidity", "zig": "Zig",
}
_LOW_PRIORITY = "docs/config"

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.M)


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    status: str  # added | modified | removed | renamed | copied | changed | unchanged
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_filename: str | None = None


@dataclass(frozen=True)
class Skipped:
    filename: str
    reason: str


@dataclass(frozen=True)
class CompressedDiff:
    text: str
    included: tuple[str, ...]
    deleted: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    over_budget: tuple[str, ...] = ()
    truncated: tuple[str, ...] = ()
    compressed: bool = False
    total_files: int = 0
    main_language: str | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def omitted_count(self) -> int:
        return len(self.over_budget) + len(self.skipped)

    def coverage_note(self) -> str:
        """One plain paragraph on what the analysis did not see (empty when it saw everything)."""
        parts = []
        if self.over_budget:
            parts.append(f"{len(self.over_budget)} file(s) left out to fit the token budget: "
                         + ", ".join(f"`{n}`" for n in self.over_budget[:15])
                         + (" ..." if len(self.over_budget) > 15 else ""))
        if self.truncated:
            parts.append(f"{len(self.truncated)} large file(s) analyzed in part: "
                         + ", ".join(f"`{n}`" for n in self.truncated[:10]))
        if self.skipped:
            parts.append(f"{len(self.skipped)} file(s) skipped (lockfile, generated, binary or no diff)")
        if self.deleted:
            parts.append(f"{len(self.deleted)} deleted file(s) listed by name only")
        return "; ".join(parts) + "." if parts else ""


def budget_from_env(env: Mapping[str, str] | None = None) -> int:
    raw = ((os.environ if env is None else env).get(BUDGET_ENV) or "").strip()
    if not raw:
        return DEFAULT_DIFF_TOKEN_BUDGET
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"{BUDGET_ENV} must be an integer, got {raw!r}") from e
    if value < MIN_PER_FILE_TOKENS:
        raise ValueError(f"{BUDGET_ENV} must be at least {MIN_PER_FILE_TOKENS}, got {value}")
    return value


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# --- extraction --------------------------------------------------------------------------------

def extract_files(tool_texts: Iterable[str]) -> tuple[ChangedFile, ...]:
    """Changed files found in this turn's tool output, de-duplicated by filename (first wins,
    except that a later copy with a patch replaces one without)."""
    found: dict[str, ChangedFile] = {}
    for text in tool_texts:
        for changed in _files_in_text(text):
            current = found.get(changed.filename)
            if current is None or (current.patch is None and changed.patch is not None):
                found[changed.filename] = changed
    return tuple(found.values())


def _files_in_text(text: str) -> Iterator[ChangedFile]:
    for value in json_values(text):
        yield from _files_in_json(value)
    if "diff --git" in text:
        yield from parse_unified_diff(text)


def json_values(text: str) -> Iterator[Any]:
    """Every JSON document in the text: the whole text, or each `{`/`[`-started block."""
    stripped = text.strip()
    try:
        yield json.loads(stripped)
        return
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    index = 0
    while True:
        starts = [i for i in (stripped.find("{", index), stripped.find("[", index)) if i >= 0]
        if not starts:
            return
        start = min(starts)
        try:
            value, end = decoder.raw_decode(stripped, start)
        except ValueError:
            index = start + 1
            continue
        yield value
        index = end


def _files_in_json(value: Any) -> Iterator[ChangedFile]:
    if isinstance(value, str) and ("\"filename\"" in value or "diff --git" in value):
        yield from _files_in_text(value)  # JSON-in-a-string, as some connectors wrap results
    elif isinstance(value, list):
        for item in value:
            yield from _files_in_json(item)
    elif isinstance(value, Mapping):
        changed = _as_changed_file(value)
        if changed is not None:
            yield changed
            return
        for item in value.values():
            yield from _files_in_json(item)


def _as_changed_file(raw: Mapping[str, Any]) -> ChangedFile | None:
    name = raw.get("filename")
    if not isinstance(name, str) or not name.strip():
        return None
    if not any(key in raw for key in ("patch", "status", "additions", "changes")):
        return None
    patch = raw.get("patch")
    previous = raw.get("previous_filename")
    return ChangedFile(
        filename=name.strip(),
        status=str(raw.get("status") or "modified"),
        additions=_int(raw.get("additions")),
        deletions=_int(raw.get("deletions")),
        patch=patch if isinstance(patch, str) and patch.strip() else None,
        previous_filename=previous if isinstance(previous, str) else None,
    )


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_unified_diff(text: str) -> Iterator[ChangedFile]:
    """Files from raw `git diff` text (the `.diff` media type)."""
    matches = list(_DIFF_GIT.finditer(text))
    for n, match in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
        block = text[match.end():end]
        hunk_at = block.find("\n@@")
        patch = block[hunk_at + 1:].rstrip("\n") if hunk_at >= 0 else None
        status = "removed" if "\ndeleted file mode" in block else "added" if "\nnew file mode" in block else "modified"
        lines = (patch or "").splitlines()
        yield ChangedFile(
            filename=match.group(2).strip(), status=status,
            additions=sum(1 for ln in lines if ln.startswith("+")),
            deletions=sum(1 for ln in lines if ln.startswith("-")),
            patch=patch,
        )


# --- classification ----------------------------------------------------------------------------

def skip_reason(changed: ChangedFile) -> str | None:
    """Why a file is not worth the model's tokens, or None to keep it."""
    path = changed.filename.lower()
    name = PurePosixPath(path).name
    if name in _LOCKFILES:
        return "lockfile"
    if any(path.endswith(s) for s in _GENERATED_SUFFIXES) or any(f"/{d}" in f"/{path}" for d in _GENERATED_DIRS):
        return "generated"
    if _extension(path) in _BINARY_EXTENSIONS:
        return "binary"
    if changed.patch is None and changed.status != "removed":
        return "no diff (binary or too large for the API)"
    return None


def language_of(filename: str) -> str:
    return _LANGUAGES.get(_extension(filename.lower()), _LOW_PRIORITY)


def _extension(path: str) -> str:
    suffix = PurePosixPath(path).suffix
    return suffix[1:] if suffix else ""


def rank_files(files: Sequence[ChangedFile]) -> tuple[tuple[ChangedFile, ...], str | None]:
    """Main languages first (by changed lines in this PR), larger diffs first within a language."""
    weight: dict[str, int] = {}
    for f in files:
        lang = language_of(f.filename)
        weight[lang] = weight.get(lang, 0) + f.additions + f.deletions + 1
    ordered_langs = sorted(weight, key=lambda lang: (lang == _LOW_PRIORITY, -weight[lang], lang))
    rank = {lang: n for n, lang in enumerate(ordered_langs)}
    ordered = sorted(files, key=lambda f: (rank[language_of(f.filename)], -len(f.patch or ""), f.filename))
    main = next((lang for lang in ordered_langs if lang != _LOW_PRIORITY), None)
    return tuple(ordered), main


# --- hunks -------------------------------------------------------------------------------------

def number_hunks(patch: str) -> str:
    """Prefix new-side line numbers: `  12 + code` (added), `  13   code` (context), `     - code`
    (removed, no new-side number). Hunk headers are kept."""
    out: list[str] = []
    new_line = 0
    for line in patch.splitlines():
        header = _HUNK_HEADER.match(line)
        if header:
            new_line = int(header.group(3))
            out.append(line)
            continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            continue
        if line.startswith("-"):
            out.append(f"{'':>5} - {line[1:]}")
        elif line.startswith("+"):
            out.append(f"{new_line:>5} + {line[1:]}")
            new_line += 1
        else:
            out.append(f"{new_line:>5}   {line[1:] if line.startswith(' ') else line}")
            new_line += 1
    return "\n".join(out)


def drop_deletion_only_hunks(patch: str) -> str:
    """Keep only hunks that add something (PR-Agent's compressed mode)."""
    hunks: list[list[str]] = []
    preamble: list[str] = []
    for line in patch.splitlines():
        if _HUNK_HEADER.match(line):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
        else:
            preamble.append(line)
    kept = [h for h in hunks if any(ln.startswith("+") for ln in h[1:])]
    return "\n".join([*preamble, *(ln for h in kept for ln in h)])


def truncate_patch(text: str, max_tokens: int) -> tuple[str, bool]:
    """Cut at a line boundary under `max_tokens`; the note says how much was left out."""
    if estimate_tokens(text) <= max_tokens:
        return text, False
    budget = max_tokens * CHARS_PER_TOKEN
    lines = text.splitlines()
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    omitted = len(lines) - len(kept)
    return "\n".join([*kept, f"[... {omitted} more line(s) of this file not shown: token budget ...]"]), True


# --- packing -----------------------------------------------------------------------------------

def _section(changed: ChangedFile, body: str) -> str:
    renamed = f" (renamed from `{changed.previous_filename}`)" if changed.previous_filename else ""
    header = (f"## File: `{changed.filename}`{renamed} [{changed.status}, "
              f"+{changed.additions}/-{changed.deletions}, {language_of(changed.filename)}]")
    return f"{header}\n{body}"


def compress(files: Sequence[ChangedFile], token_budget: int = DEFAULT_DIFF_TOKEN_BUDGET) -> CompressedDiff:
    """The diff text the model sees, packed into `token_budget` (see the module docstring)."""
    deleted = tuple(f.filename for f in files if f.status == "removed")
    skipped = tuple(Skipped(f.filename, r) for f in files if f.status != "removed" and (r := skip_reason(f)))
    skipped_names = {s.filename for s in skipped}
    usable = [f for f in files if f.status != "removed" and f.filename not in skipped_names]
    ranked, main = rank_files(usable)
    full = [(f, _section(f, number_hunks(f.patch or ""))) for f in ranked]
    total = sum(estimate_tokens(text) for _, text in full)
    base = CompressedDiff(text="", included=(), deleted=deleted, skipped=skipped, total_files=len(files),
                          main_language=main)
    if total <= token_budget:
        return replace(base, text=_join(t for _, t in full), included=tuple(f.filename for f, _ in full))
    return _pack(ranked, token_budget, base)


def _pack(ranked: Sequence[ChangedFile], soft: int, base: CompressedDiff) -> CompressedDiff:
    hard = int(soft * (1 + HARD_BUDGET_MARGIN))
    per_file = max(MIN_PER_FILE_TOKENS, int(soft * PER_FILE_SHARE))
    sections: list[str] = []
    included: list[str] = []
    truncated: list[str] = []
    left_out: list[str] = []
    used = 0
    for changed in ranked:
        if used >= soft:
            left_out.append(changed.filename)
            continue
        body = number_hunks(drop_deletion_only_hunks(changed.patch or ""))
        if not body.strip():
            left_out.append(changed.filename)  # only deletions: nothing new to review
            continue
        body, cut = truncate_patch(body, per_file)
        section = _section(changed, body)
        cost = estimate_tokens(section)
        if used + cost > hard:
            left_out.append(changed.filename)
            continue
        sections.append(section)
        included.append(changed.filename)
        truncated.extend([changed.filename] if cut else [])
        used += cost
    return replace(base, text=_join(sections), included=tuple(included), over_budget=tuple(left_out),
                   truncated=tuple(truncated), compressed=True)


def _join(sections: Iterable[str]) -> str:
    return "\n\n".join(sections)


def render_for_model(diff: CompressedDiff) -> str:
    """The diff plus the lists of files it does not show (PR-Agent appends the same lists)."""
    blocks = [diff.text] if diff.text else []
    if diff.deleted:
        blocks.append("Deleted files:\n" + "\n".join(f"- {n}" for n in diff.deleted))
    if diff.over_budget:
        blocks.append("Additional changed files (not shown, insufficient token budget):\n"
                      + "\n".join(f"- {n}" for n in diff.over_budget))
    if diff.skipped:
        blocks.append("Skipped files (not reviewed):\n"
                      + "\n".join(f"- {s.filename} ({s.reason})" for s in diff.skipped))
    return "\n\n".join(blocks)

