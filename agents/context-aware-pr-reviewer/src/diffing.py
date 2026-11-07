"""Pure: the pull request's diff in code. Unified-diff parsing (whole diffs and per-file patches),
hunk ranges, the skip rules (lockfiles, generated, minified, vendored, binary), line numbering for the
model, and chunking at file (then hunk) boundaries. Nothing is truncated silently: every file is
either in a chunk, skipped with a reason, or listed as not reviewed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_CHUNK_CHARS = 6_000
MAX_CHUNKS = 4
MAX_PASTED_DIFF_CHARS = 100_000

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_LOCKFILES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "poetry.lock", "go.sum",
                        "gemfile.lock", "composer.lock", "uv.lock", "pipfile.lock", "npm-shrinkwrap.json",
                        "packages.lock.json", "podfile.lock", "mix.lock", "flake.lock"})
_GENERATED_SUFFIX = (".min.js", ".min.css", ".map", "_pb2.py", "_pb2_grpc.py", ".pb.go", ".generated.ts",
                     ".g.dart", ".snap")
_GENERATED_DIRS = ("dist/", "build/", "generated/", "__generated__/", "gen/", "out/")
_VENDORED_DIRS = ("vendor/", "node_modules/", "third_party/", "third-party/", "external/")
_BINARY_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".jar", ".woff", ".woff2",
                  ".ttf", ".otf", ".mp4", ".mov", ".webp", ".so", ".dylib", ".dll", ".exe", ".bin", ".svgz")


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: tuple[str, ...]  # the raw lines after the header, each starting with " ", "+", "-" or "\\"

    @property
    def new_range(self) -> range:
        """New-side lines this hunk covers; a pure deletion anchors on its start line."""
        return range(self.new_start, self.new_start + max(self.new_len, 1))


@dataclass(frozen=True)
class FilePatch:
    path: str
    patch: str
    status: str = "modified"
    previous_path: str = ""

    @property
    def hunks(self) -> tuple[Hunk, ...]:
        return parse_hunks(self.patch)

    @property
    def changed_lines(self) -> int:
        return sum(1 for h in self.hunks for line in h.lines if line[:1] in "+-")

    def added_text(self) -> str:
        return "\n".join(line[1:] for h in self.hunks for line in h.lines if line.startswith("+"))


@dataclass(frozen=True)
class Chunk:
    files: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Plan:
    chunks: tuple[Chunk, ...]
    skipped: tuple[tuple[str, str], ...] = ()        # (path, reason)
    not_reviewed: tuple[str, ...] = ()              # beyond MAX_CHUNKS
    reviewed: tuple[str, ...] = field(default=())


def parse_hunks(patch: str) -> tuple[Hunk, ...]:
    hunks: list[Hunk] = []
    header: tuple[int, int, int, int] | None = None
    body: list[str] = []
    for line in (patch or "").splitlines():
        m = _HUNK.match(line)
        if m:
            if header:
                hunks.append(Hunk(*header, tuple(body)))
            a, b, c, d = m.groups()
            header = (int(a), int(b) if b is not None else 1, int(c), int(d) if d is not None else 1)
            body = []
        elif header is not None and line[:1] in (" ", "+", "-", "\\", ""):
            body.append(line if line else " ")
    if header:
        hunks.append(Hunk(*header, tuple(body)))
    return tuple(hunks)


def parse_unified(text: str) -> tuple[FilePatch, ...]:
    """A whole `diff --git` diff split into per-file patches (renames and deletions kept)."""
    starts = list(_DIFF_GIT.finditer(text or ""))
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[m.start():end]
        old, new = m.group(1), m.group(2)
        status = ("removed" if "\ndeleted file mode" in block else "added" if "\nnew file mode" in block
                  else "renamed" if old != new or "\nrename from " in block else "modified")
        if "\nBinary files " in block or "GIT binary patch" in block:
            status = "binary"
        at = block.find("\n@@")
        patch = block[at + 1:] if at != -1 else ""
        out.append(FilePatch(new if status != "removed" else old, patch, status, old if old != new else ""))
    return tuple(out)


def in_hunk(patch: FilePatch | None, line: int) -> bool:
    return patch is not None and line > 0 and any(line in h.new_range for h in patch.hunks)


def skip_reason(f: FilePatch) -> str:
    path = f.path.casefold()
    name = path.rsplit("/", 1)[-1]
    if name in _LOCKFILES:
        return "lockfile"
    if f.status == "binary" or path.endswith(_BINARY_SUFFIX):
        return "binary"
    if any(path.startswith(d) or f"/{d}" in path for d in _VENDORED_DIRS):
        return "vendored"
    if path.endswith(_GENERATED_SUFFIX) or any(path.startswith(d) or f"/{d}" in path for d in _GENERATED_DIRS):
        return "generated or minified"
    if not f.patch.strip():
        return "no patch text (binary, too large or rename only)"
    return ""


def numbered(f: FilePatch, hunks: tuple[Hunk, ...] | None = None) -> str:
    """The patch with new-side line numbers added in code: `  47 +code`, `     -removed`."""
    out = [f"### {f.path} ({f.status}{', was ' + f.previous_path if f.previous_path else ''})"]
    for h in hunks if hunks is not None else f.hunks:
        out.append(f"@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len} @@")
        n = h.new_start
        for line in h.lines:
            mark, content = line[:1], line[1:]
            if mark == "-":
                out.append(f"      -{content}")
            elif mark == "\\":
                continue
            else:
                out.append(f"{n:>5} {mark}{content}")
                n += 1
    return "\n".join(out)


def plan(files: tuple[FilePatch, ...]) -> Plan:
    """Chunks of at most MAX_CHUNK_CHARS at file boundaries (a large file at hunk boundaries), the
    biggest files first; at most MAX_CHUNKS, the rest listed as not reviewed."""
    skipped = tuple((f.path, r) for f in files if (r := skip_reason(f)))
    keep = sorted((f for f in files if not skip_reason(f)), key=lambda f: -f.changed_lines)
    pieces = [(f.path, text) for f in keep for text in _pieces(f)]
    chunks: list[tuple[list[str], list[str]]] = []
    for path, text in pieces:
        if chunks and sum(len(t) for t in chunks[-1][1]) + len(text) <= MAX_CHUNK_CHARS:
            chunks[-1][0].append(path)
            chunks[-1][1].append(text)
        else:
            chunks.append(([path], [text]))
    kept = chunks[:MAX_CHUNKS]
    reviewed = tuple(dict.fromkeys(p for paths, _ in kept for p in paths))
    rest = tuple(dict.fromkeys(p for paths, _ in chunks[MAX_CHUNKS:] for p in paths if p not in reviewed))
    built = tuple(Chunk(tuple(dict.fromkeys(paths)), "\n\n".join(texts)) for paths, texts in kept)
    return Plan(built, skipped, rest, reviewed)


def _pieces(f: FilePatch) -> list[str]:
    whole = numbered(f)
    if len(whole) <= MAX_CHUNK_CHARS:
        return [whole]
    out: list[str] = []
    for h in f.hunks:
        text = numbered(f, (h,))
        if len(text) <= MAX_CHUNK_CHARS:
            out.append(text)
        else:
            out += _split_lines(text)
    return out


def _split_lines(text: str) -> list[str]:
    lines, out, current = text.splitlines(), [], []
    head = lines[0]
    for line in lines[1:]:
        if current and len("\n".join([head, *current, line])) > MAX_CHUNK_CHARS:
            out.append("\n".join([head, *current]))
            current = []
        current.append(line[:MAX_CHUNK_CHARS - len(head) - 10])
    if current:
        out.append("\n".join([head, *current]))
    return out


def pasted_diff(text: str) -> tuple[tuple[FilePatch, ...], str]:
    """(files, the text outside the diff) from a pasted message; empty files when no diff is pasted."""
    body = (text or "")[:MAX_PASTED_DIFF_CHARS]
    first = body.find("diff --git ")
    if first == -1:
        return (), body
    return parse_unified(body[first:]), body[:first]
