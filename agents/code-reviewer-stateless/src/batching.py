"""Pure: what each changed file is, which files are reviewed and in what order, and the numbered diff text
the model sees (`+ NEW:<n> | text` for added lines; code assigns every number).

Rank score = 3 x source with added lines + 2 x migration/config + 1 x test - 2 x docs/generated; ties by size.
Batches hold at most BATCH_CHARS of numbered diff, at most MAX_BATCHES per turn (one: the crew reviews it in one
task), filled in rank order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from diff_parse import FileDiff

BATCH_CHARS = 28_000   # the one reviewer task reads at most this much numbered diff
MAX_BATCHES = 1        # one crew per turn: files that do not fit are listed as "(size cap)"
MINIFIED_LINE_CHARS = 500
SKIPPED_KINDS = ("lock", "generated")

_LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "poetry.lock", "gemfile.lock",
              "go.sum", "composer.lock", "uv.lock", "pipfile.lock", "npm-shrinkwrap.json", "bun.lockb"}
_GENERATED = re.compile(r"\.min\.(?:js|css)$|\.map$|_pb2(?:_grpc)?\.pyi?$|\.pb\.go$|\.generated\.|"
                        r"(?:^|/)(?:dist|build|vendor|node_modules|__generated__)/|(?:^|/)generated/", re.IGNORECASE)
_TEST = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]+\.py$|_test\.(?:py|go|rb)$|"
                   r"\.(?:test|spec)\.[jt]sx?$|Test\.java$|_spec\.rb$", re.IGNORECASE)
_MIGRATION = re.compile(r"(?:^|/)migrations?/|\.sql$", re.IGNORECASE)
_DOCS = re.compile(r"\.(?:md|rst|txt|adoc)$|(?:^|/)docs?/", re.IGNORECASE)
_CONFIG = re.compile(r"\.(?:json|ya?ml|toml|ini|cfg|conf|env|properties|xml)$|(?:^|/)(?:Dockerfile|Makefile)$",
                     re.IGNORECASE)
_KIND_WEIGHT = {"source": 3, "migration": 2, "config": 2, "test": 1, "docs": -2, "generated": -2, "lock": -2}


def kind_of(f: FileDiff) -> str:
    """source | test | config | docs | lock | generated | migration."""
    name = PurePosixPath(f.path).name.casefold()
    if name in _LOCKFILES:
        return "lock"
    if _GENERATED.search(f.path) or any(len(t) > MINIFIED_LINE_CHARS for t in f.added.values()):
        return "generated"
    for kind, rx in (("test", _TEST), ("migration", _MIGRATION), ("docs", _DOCS), ("config", _CONFIG)):
        if rx.search(f.path):
            return kind
    return "source"


def skip_reason(f: FileDiff) -> str | None:
    if f.binary:
        return "binary"
    kind = kind_of(f)
    if kind == "lock":
        return "lockfile"
    if kind == "generated":
        return "generated or minified"
    return None


def score(f: FileDiff) -> int:
    kind = kind_of(f)
    base = _KIND_WEIGHT[kind]
    return 0 if kind == "source" and not f.added else base


def ranked(files: tuple[FileDiff, ...]) -> list[FileDiff]:
    return sorted(files, key=lambda f: (-score(f), -(f.added_count + f.removed_count), f.path))


def describe(f: FileDiff) -> str:
    extra = f", renamed from {f.old_path}" if f.status == "renamed" else ""
    return f"{f.path}: {kind_of(f)}, {f.status}{extra}, +{f.added_count} -{f.removed_count}"


def numbered(f: FileDiff, budget: int = BATCH_CHARS) -> tuple[str, bool]:
    """The file's numbered diff, cut at whole hunks to fit `budget`; (text, truncated)."""
    head = f"### file: {describe(f)}"
    out, used = [head], len(head) + 1
    for hunk in f.hunks:
        rows = [hunk.header] + [_row(ln) for ln in hunk.lines]
        size = sum(len(r) + 1 for r in rows)
        if used + size > budget and len(out) > 1:
            return "\n".join(out), True
        out.extend(rows)
        used += size
    return "\n".join(out)[:budget], used > budget


def _row(ln) -> str:
    if ln.kind == "+":
        return f"+ NEW:{ln.new_no} | {ln.text}"
    if ln.kind == " ":
        return f"  ctx:{ln.new_no} | {ln.text}"
    return f"-          | {ln.text}"


@dataclass(frozen=True)
class Plan:
    batches: tuple[tuple[str, tuple[str, ...]], ...]   # (numbered text, paths in it)
    skipped: tuple[str, ...]                            # "path (reason)"
    partial: tuple[str, ...]                            # paths reviewed only in part


def plan(files: tuple[FileDiff, ...]) -> Plan:
    skipped: list[str] = []
    partial: list[str] = []
    batches: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for f in ranked(files):
        reason = skip_reason(f)
        if reason:
            skipped.append(f"{f.path} ({reason})")
            continue
        if not f.added:
            continue
        text, cut = numbered(f)
        if cut:
            partial.append(f.path)
        slot = next((n for n, (texts, _) in enumerate(batches) if _size(texts) + len(text) + 2 <= BATCH_CHARS), None)
        if slot is None and len(batches) >= MAX_BATCHES:
            skipped.append(f"{f.path} (size cap)")
            continue
        if slot is None:
            batches.append(((text,), (f.path,)))
        else:
            texts, paths = batches[slot]
            batches[slot] = ((*texts, text), (*paths, f.path))
    done = tuple(("\n\n".join(texts), paths) for texts, paths in batches)
    return Plan(done, tuple(skipped), tuple(partial))


def _size(texts: tuple[str, ...]) -> int:
    return sum(len(t) + 2 for t in texts)
