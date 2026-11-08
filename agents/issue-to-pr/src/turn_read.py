"""Graph nodes, read side: intake (code), discover (tools), issue (read), candidates (code + tools).
Each node takes the checkpointed state and this turn's Scope and returns a state update."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import candidates as cand
import gather
import hitl
import localization as loc
import prompts
import smalltalk
import steps
import triage
from checks import Dropped
from codehost import CodeHost
from constraints import Constraints
from discovery import Catalog, discover
from inputs import Incoming, PastedFile, RepoRef, Request, parse_request, repos_named
from llm import LlmError, TurnDeps
from shapes import IssueDoc
from terms import extract

_FOLLOWUP_DIFF = re.compile(r"^\s*(?:what did you change|show (?:me )?the diff|what changed)\b", re.IGNORECASE)


@dataclass
class Scope:
    """One turn's inputs and live objects (never persisted)."""

    deps: TurnDeps
    incoming: Incoming
    pending: hitl.Pending | None = None
    resume_repo: RepoRef | None = None
    request: Request | None = None
    host: CodeHost | None = None
    catalog: Catalog | None = None
    dropped: Dropped = field(default_factory=Dropped)
    revision: str = ""
    forced_file: str = ""


def end(reply: str, **extra) -> dict:
    return {"reply": reply, "route": "", **extra}


def repo_dict(repo: RepoRef | None) -> dict | None:
    return None if repo is None else {"owner": repo.owner, "name": repo.name, "ref": repo.ref}


def repo_of(data: dict | None) -> RepoRef | None:
    return None if not data else RepoRef(data["owner"], data["name"], data.get("ref"))


def pause(pending: hitl.Pending, **extra) -> dict:
    return {"reply": "", "route": "", "pause": pending.to_metadata(), "pause_text": pending.text, **extra}


async def intake(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return end(prompts.NO_TRACEPARENT_REPLY)
    if scope.pending is not None:
        return _resume_route(state, scope)
    text = scope.incoming.text
    if state.get("envelope") and _FOLLOWUP_DIFF.match(text):
        return {"route": "followup"}
    request = parse_request(text)
    if scope.resume_repo is not None:
        request = Request(request.issue_text, scope.resume_repo, request.issue_number, request.files, request.hints,
                          request.from_envelope, request.rules, request.suspects, request.locations)
    scope.request = request
    unanchored = request.repo is None and request.issue_number is None and not request.files \
        and not request.from_envelope
    if unanchored and not await _in_scope(scope, text):
        return end(smalltalk.OFF_TOPIC_REPLY)
    if not request.has_issue:
        return end(prompts.NEED_ISSUE)
    if request.repo is None and not request.files:
        options = (*(repos_named(text) or ()), hitl.CANCEL)
        return pause(hitl.Pending("need_repo", text, options, scope.incoming.wants_json,
                                  header="Which repository?", text=prompts.NEED_REPO))
    fresh = {"plan": None, "changes": [], "session_id": None, "revisions": 0, "envelope": None, "originals": {},
             "localization": None, "picks": [], "symbols": [], "locations": []}
    return {"route": "discover", "hints": list(request.hints), "notes": [], "constraints": request.rules.to_dict(),
            "request_text": text, **fresh}


async def _in_scope(scope: Scope, text: str) -> bool:
    try:
        return await steps.gate(scope.deps, text)
    except LlmError:
        return True


def _resume_route(state: dict, scope: Scope) -> dict:
    kind = scope.pending.kind
    if kind in ("approval", "diff_only"):
        return {"route": "approve"}
    if kind == "closed":
        scope.request = stored_request(state)
        return {"route": "rediscover"}
    if kind == "pick_file":
        return {"route": "rediscover"}
    return end(prompts.NOT_WAITING)


def stored_request(state: dict) -> Request:
    pasted = tuple(PastedFile(p, d.get("full") or d.get("text") or "") for p, d in (state.get("files") or {}).items()) \
        if state.get("pasted_only") else ()
    return Request("", repo_of(state.get("repo")), state.get("issue_number"), pasted, tuple(state.get("hints") or ()),
                   rules=Constraints.from_dict(state.get("constraints")))


async def rediscover_node(state: dict, scope: Scope) -> dict:
    """A resumed turn needs this turn's live catalog and code host again (they are never persisted)."""
    scope.request = scope.request or stored_request(state)
    if not state.get("pasted_only"):
        catalog, failure = await discover(scope.deps)
        if catalog is None or not catalog.has_code_host:
            return end(failure or prompts.NO_SOURCE_REPLY)
        scope.catalog = catalog
        scope.host = CodeHost(scope.deps, catalog)
    return {"route": "candidates" if scope.pending.kind == "closed" else "fetch"}


async def discover_node(state: dict, scope: Scope) -> dict:
    request = scope.request
    catalog, failure = await discover(scope.deps)
    if catalog is not None and catalog.has_code_host and request.repo is not None:
        scope.catalog = catalog
        scope.host = CodeHost(scope.deps, catalog)
        return {"route": "issue", "pasted_only": False, "can_open_pr": catalog.can_open_pr}
    if request.files:
        return {"route": "issue", "pasted_only": True, "can_open_pr": False}
    return end(failure or prompts.NO_SOURCE_REPLY)


async def issue_node(state: dict, scope: Scope) -> dict:
    request = scope.request
    pasted = request.issue_text
    fetched: IssueDoc | None = None
    closed = False
    if request.issue_number is not None and scope.host is not None and request.repo is not None:
        fetched, closed = await gather.read_issue(scope.host, request.repo, request.issue_number)
    if fetched is None and not pasted:
        why = "" if scope.host is None or scope.host.has("issue") else " (no issue-reading tool is available)"
        repo = request.repo.full if request.repo else "the repository"
        return end(prompts.ISSUE_UNREADABLE.format(number=request.issue_number, repo=repo, why=why))
    doc = _merge(fetched, pasted)
    number = request.issue_number
    update = {"issue": _issue_dict(doc), "issue_number": number, "repo": repo_dict(request.repo)}
    if not doc.body.strip() and len(doc.title.split()) < 4:
        return end(prompts.EMPTY_ISSUE.format(number=number or ""), **update)
    if triage.kind_override(doc.title, doc.text(), doc.labels, []) == "question":
        return end(prompts.QUESTION_ISSUE.format(number=number or ""), **update)
    if closed:
        pending = hitl.Pending("closed", options=(hitl.CONTINUE, hitl.STOP), wants_json=scope.incoming.wants_json,
                               header="Continue on a closed issue?", text=prompts.CLOSED.format(number=number))
        return pause(pending, **update)
    return {"route": "candidates", **update}


async def candidates_node(state: dict, scope: Scope) -> dict:
    request = scope.request
    issue = issue_doc(state["issue"])
    terms = extract(issue.title, issue.text())
    query = gather.query_tokens(issue.text(), request.hints)
    if state.get("pasted_only"):
        return _pasted(request.files, terms, query, request.hints)
    ref = await gather.read_ref(scope.host, request.repo)
    found = await gather.collect(scope.host, request.repo, ref, terms, query, request.hints)
    listing = list(found.listing)
    if not listing:
        why = " (no repository listing tool is available)" if not scope.host.has("list") else \
            " (stopped at the tool-call or time budget)" if scope.host.coverage.budget_hit else ""
        return end(prompts.NO_CANDIDATES.format(repo=request.repo.full, why=why))
    if ref is None:
        options = ("main", "master", hitl.CANCEL)
        return pause(hitl.Pending("base_branch", state.get("request_text") or "", options,
                                  scope.incoming.wants_json, header="Which branch should the PR target?",
                                  text=prompts.NEED_BASE))
    base = {"ref": ref, "listing": listing, "files": {}, "search_terms": list(terms.ranked[:10])}
    if request.suspects:
        record = loc.from_envelope(request.suspects, request.locations, listing)
        if record is not None:
            return {"route": "fetch", "localization": record, "candidates": [], **base}
    return {"route": "pick_files", "candidates": [c.to_dict() for c in found.candidates], **base}


def _pasted(files: tuple[PastedFile, ...], terms, query, hints) -> dict:
    paths = [f.path for f in files]
    named = cand.mentioned(terms.paths, paths)
    built = cand.build(paths, {}, named, query, hints)
    kept = {c.path for c in built}
    extra = [cand.Candidate(p, 0.0, 0, 0, p in named) for p in paths if p not in kept and not cand.is_excluded(p)]
    return {"route": "pick_files", "ref": None, "listing": paths,
            "files": {f.path: gather.visible(f.text).to_dict() for f in files},
            "candidates": [c.to_dict() for c in (*built, *extra)], "search_terms": list(terms.ranked[:10])}


def _merge(fetched: IssueDoc | None, pasted: str) -> IssueDoc:
    if fetched is None:
        lines = pasted.strip().split("\n", 1)
        return IssueDoc(lines[0][:200], lines[1] if len(lines) > 1 else "")
    body = f"{fetched.body}\n\n{pasted}".strip() if pasted else fetched.body
    return IssueDoc(fetched.title, body, fetched.labels, fetched.comments, fetched.number)


def _issue_dict(doc: IssueDoc) -> dict:
    return {"title": doc.title, "body": doc.body, "labels": list(doc.labels), "comments": list(doc.comments),
            "number": doc.number}


def issue_doc(data: dict) -> IssueDoc:
    return IssueDoc(data.get("title") or "", data.get("body") or "", tuple(data.get("labels") or ()),
                    tuple(data.get("comments") or ()), data.get("number"))
