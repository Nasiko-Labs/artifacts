"""Reading steps (tools + code): the issue and its comments, the default branch, the candidate files (top-level
listing, code searches, directory expansion, BM25), and the file reads behind the skeletons."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import candidates as cand
import shapes
from codehost import COMMENTS_PAGE, SEARCH_PAGE, CodeHost
from inputs import RepoRef
from terms import Terms, tokens

MAX_EXPAND_ROUNDS = 2
MAX_FILE_READS = 6
VISIBLE_CHARS = 12_000
MAX_LISTING = 3_000


@dataclass(frozen=True)
class Gathered:
    listing: tuple[str, ...]
    candidates: tuple[cand.Candidate, ...]


@dataclass(frozen=True)
class FileDoc:
    text: str            # the visible part only
    truncated: bool

    def to_dict(self) -> dict:
        return {"text": self.text, "truncated": self.truncated}

    @staticmethod
    def from_dict(data: Mapping) -> FileDoc:
        return FileDoc(str(data.get("text") or ""), bool(data.get("truncated")))


def visible(text: str) -> FileDoc:
    """At most 12k chars, cut at the last full line; lines beyond are never claimed."""
    if len(text) <= VISIBLE_CHARS:
        return FileDoc(text, False)
    head = text[:VISIBLE_CHARS]
    cut = head.rfind("\n")
    return FileDoc(head[:cut] if cut > 0 else head, True)


async def read_issue(host: CodeHost, repo: RepoRef, number: int) -> shapes.IssueDoc | None:
    values = {"owner": repo.owner, "repo": repo.name, "number": number}
    issue_data, comment_data = await asyncio.gather(
        host.call("issue", **values), host.call("comments", **values, page=COMMENTS_PAGE))
    issue = shapes.issue_from(issue_data) if issue_data is not None else None
    if issue is None:
        return None
    comments = shapes.comments_from(comment_data) if comment_data is not None else ()
    return shapes.IssueDoc(issue.title, issue.body, issue.labels, comments, issue.number or number)


async def read_ref(host: CodeHost, repo: RepoRef) -> str | None:
    if repo.ref:
        return repo.ref
    data = await host.call("repo", owner=repo.owner, repo=repo.name)
    return shapes.default_branch(data) if data is not None else None


async def collect(host: CodeHost, repo: RepoRef, ref: str | None, terms: Terms, query: Sequence[str],
                  hints: Sequence[str]) -> Gathered:
    base = {"owner": repo.owner, "repo": repo.name, "ref": ref}
    files, dirs = await _list(host, base, "")
    listed_dirs = {""}
    hit_lists = await asyncio.gather(*(host.call("search", **base, query=t, page=SEARCH_PAGE)
                                       for t in terms.searches))
    hits: dict[str, int] = {}
    for data in hit_lists:
        if data is None:
            continue
        host.coverage.searches += 1
        for path in dict.fromkeys(shapes.search_paths(data)):
            hits = {**hits, path: hits.get(path, 0) + 1}
    for _ in range(MAX_EXPAND_ROUNDS):
        todo = cand.to_expand(dirs, query, hits, hints, listed_dirs)
        if not todo:
            break
        listed_dirs |= set(todo)
        for more_files, more_dirs in await asyncio.gather(*(_list(host, base, d) for d in todo)):
            files, dirs = [*files, *more_files], [*dirs, *more_dirs]
    listing = tuple(dict.fromkeys([*files, *hits]))[:MAX_LISTING]
    named = cand.mentioned(terms.paths, listing)
    return Gathered(listing, cand.build(listing, hits, named, query, hints))


async def read_files(host: CodeHost, repo: RepoRef, ref: str | None, paths: Sequence[str],
                     have: Mapping[str, FileDoc]) -> dict[str, FileDoc]:
    """The requested files, reusing those already read this conversation."""
    wanted = [p for p in paths if p not in have][:MAX_FILE_READS]
    datas = await asyncio.gather(*(host.call("file", owner=repo.owner, repo=repo.name, path=p, ref=ref)
                                   for p in wanted))
    out = {p: have[p] for p in paths if p in have}
    for path, data in zip(wanted, datas, strict=True):
        text = shapes.file_text(data) if data is not None else None
        if text is None:
            continue
        doc = visible(text)
        host.coverage.files_read += 1
        host.coverage.truncated += int(doc.truncated)
        out = {**out, path: doc}
    return out


async def _list(host: CodeHost, base: Mapping, path: str) -> tuple[list[str], list[str]]:
    data = await host.call("list", **base, path=path)
    if data is None:
        return [], []
    host.coverage.dirs_listed += 1
    return shapes.entries(data, path)


def query_tokens(issue_text: str, hints: Sequence[str]) -> list[str]:
    return tokens(issue_text) + [t for h in hints for t in tokens(h)]
