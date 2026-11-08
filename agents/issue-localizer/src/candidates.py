"""Pure: the candidate file set. Search-hit paths ∪ the top BM25 paths ∪ paths named in the issue, with
vendored, generated, binary, lock and minified files removed, capped at 150. Also which directories to
expand next, and which listed paths the issue mentions."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

import bm25
from terms import path_tokens, tokens

MAX_CANDIDATES = 150
TOP_BM25 = 30
MAX_EXPAND_PER_ROUND = 4

_EXCLUDED_DIRS = ("vendor/", "node_modules/", "third_party/", "dist/", "build/", ".git/", "__pycache__/",
                  "site-packages/", "bower_components/", ".venv/", "venv/", "target/", "coverage/")
_LOCK_FILES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
                         "cargo.lock", "go.sum", "composer.lock", "gemfile.lock", "uv.lock"})
_BINARY = re.compile(r"\.(?:png|jpe?g|gif|bmp|ico|svg|pdf|zip|gz|tgz|tar|jar|war|so|dll|dylib|exe|bin|woff2?|ttf|"
                     r"eot|mp[34]|mov|webm|wasm|pyc|class|o|a|db|sqlite)$", re.IGNORECASE)
_GENERATED = re.compile(r"(?:\.min\.(?:js|css)$|_pb2(?:_grpc)?\.py$|\.pb\.go$|\.generated\.|\.g\.dart$|"
                        r"\.snap$|\.map$)", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    path: str
    bm25: float = 0.0
    bm25_rank: int = 0
    hits: int = 0
    mentioned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> Candidate:
        return Candidate(str(data["path"]), float(data.get("bm25") or 0.0), int(data.get("bm25_rank") or 0),
                         int(data.get("hits") or 0), bool(data.get("mentioned")))


def is_excluded(path: str) -> bool:
    lowered = "/" + path.casefold().lstrip("/")
    name = lowered.rsplit("/", 1)[-1]
    return (any(f"/{d}" in lowered for d in _EXCLUDED_DIRS) or name in _LOCK_FILES or bool(_BINARY.search(name))
            or bool(_GENERATED.search(name)))


def mentioned(issue_paths: Iterable[str], listing: Iterable[str]) -> frozenset[str]:
    """Listed paths the issue names: by full path, by path suffix (stack frames), or by unique basename."""
    listed = tuple(listing)
    found = set()
    for raw in issue_paths:
        want = raw.strip("/").casefold()
        matches = [p for p in listed if p.casefold() == want or p.casefold().endswith("/" + want)
                   or want.endswith("/" + p.casefold())]
        if len(matches) > 1:  # ambiguous: keep only an exact path
            matches = [p for p in matches if p.casefold() == want]
        found.update(matches)
    return frozenset(found)


def build(listing: Sequence[str], hits: Mapping[str, int], named: frozenset[str], query: Sequence[str],
          hints: Sequence[str] = ()) -> tuple[Candidate, ...]:
    files = [p for p in dict.fromkeys([*listing, *hits]) if not is_excluded(p)]
    docs = {p: path_tokens(p) for p in files}
    boosted = list(query) + [t for h in hints for t in tokens(h)] * 2
    ranked = bm25.rank(docs, boosted)
    rank_of = {p: i + 1 for i, (p, s) in enumerate(ranked) if s > 0}
    score_of = dict(ranked)
    chosen = [p for p, s in ranked[:TOP_BM25] if s > 0]
    keep = list(dict.fromkeys([*[p for p in files if p in hits], *[p for p in files if p in named], *chosen]))
    in_hint = [p for p in files if any(p.startswith(h.rstrip("/") + "/") for h in hints)]
    keep = list(dict.fromkeys([*keep, *in_hint]))[:MAX_CANDIDATES]
    return tuple(Candidate(p, round(score_of.get(p, 0.0), 4), rank_of.get(p, 0), hits.get(p, 0), p in named)
                 for p in keep)


def to_expand(dirs: Sequence[str], query: Sequence[str], hit_paths: Iterable[str], hints: Sequence[str],
              done: Iterable[str]) -> tuple[str, ...]:
    """Directories worth listing next: hinted, ancestors of a search hit, or name-matching the issue."""
    finished = set(done)
    wanted = set(query)
    hit_list = list(hit_paths)
    picked = []
    for d in dirs:
        if d in finished or is_excluded(d + "/"):
            continue
        hinted = any(h.rstrip("/") == d or h.startswith(d + "/") for h in hints)
        holds_hit = any(h.startswith(d + "/") for h in hit_list)
        named = bool(set(path_tokens(d)) & wanted)
        if hinted or holds_hit or named:
            picked.append((0 if hinted else 1 if holds_hit else 2, d))
    return tuple(d for _, d in sorted(picked))[:MAX_EXPAND_PER_ROUND]
