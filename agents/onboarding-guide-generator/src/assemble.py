"""From what was gathered to the guide (PLAN.md "Method" 2-9): commands, tasks, map, start files, owners,
PRs, docs and checklist in code; the welcome and PR lessons from the two writers; then the final check
that every code span in the markdown occurs in this turn's fetched text."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import plan
import render
import sections
from deps import TurnDeps
from gather import Gathered, rules_of
from guide import Guide
from intake import Request
from reader import TurnBudget
from verify import normalise_text, unverified_spans

log = logging.getLogger("onboarding.assemble")

WRITE_DEADLINE_SECS = 270
_MD_ORDER = ("readme", "contributing", ".github/contributing", "architecture", "docs/")
REMOVED = "[removed: not found in the repository]"


def _ordered_docs(files: Mapping[str, str]) -> dict[str, str]:
    def rank(path: str) -> int:
        lowered = path.casefold()
        return next((i for i, p in enumerate(_MD_ORDER) if lowered.startswith(p)), len(_MD_ORDER))

    docs = [p for p in files if p.casefold().endswith((".md", ".rst", ".txt")) or rank(p) < 3]
    return {p: files[p] for p in sorted(docs, key=lambda p: (rank(p), p))}


async def assemble(deps: TurnDeps, budget: TurnBudget, req: Request, got: Gathered, seen_raw: str) -> Guide:
    files = {p: f.text for p, f in got.files.items()}
    docs = _ordered_docs(files)
    readme_path = next((p for p in docs if "/" not in p and p.casefold().startswith("readme")), "")
    readme = files.get(readme_path, "")
    ticket_text = f"{got.issue.title}\n{got.issue.body}" if got.issue else ""
    seen = normalise_text("\n".join((seen_raw, *files.values(), ticket_text, req.ticket_text)))
    groups = plan.commands(docs)
    task_list = plan.tasks(files)
    start = plan.rank_paths(got.hits)
    start_paths = [p for p, _ in start]
    co_path, rules = rules_of(got)
    recent = plan.recent(got.prs, deps.today)
    area = plan.relevant(recent, start_paths, got.keywords)
    owners = _owners(start_paths, rules, area)
    in_repo_docs = [{"title": p, "link": got.files[p].link, "where": "repo"} for p in docs]
    kb_docs = [{"title": d.title, "link": d.link, "where": "doc store"} for d in got.kb]
    description = got.repo.description if got.repo else ""
    welcome, lessons, dropped = await _write(deps, budget, description, readme, area[:plan.MAX_PRS], seen)
    guide = Guide(
        repo=req.repo, hire=req.hire, role=req.role, description=description, welcome=welcome,
        readme_path=readme_path,
        commands=tuple({"source": g.source, "heading": g.heading, "lines": list(g.lines)} for g in groups),
        tasks=tuple({"source": t.source, "name": t.name, "definition": t.definition} for t in task_list),
        repo_map=tuple({"path": d, "note": n} for d, n in plan.repo_map(got.listing, readme)),
        ticket_asked=req.ticket or ("pasted ticket" if req.ticket_text else ""),
        ticket={"key": got.issue.key, "title": got.issue.title, "link": got.issue.link} if got.issue else None,
        keywords=got.keywords, start_files=tuple({"path": p, "hits": n} for p, n in start), owners=owners,
        codeowners_path=co_path, rules=tuple({"pattern": r.pattern, "owners": list(r.owners)} for r in rules),
        prs=tuple(_pr(p, lessons.get(p.number, "")) for p in area[:plan.MAX_PRS]),
        recent_prs=tuple(_pr(p, "") for p in recent),
        docs=tuple(in_repo_docs + kb_docs),
        checklist=plan.checklist(req.repo, groups, task_list, got.issue is not None,
                                 [p for p in docs if p != readme_path] or ([readme_path] if readme_path else [])),
        coverage=(*got.coverage, f"CODEOWNERS found at `{co_path}`" if co_path else "no CODEOWNERS file found"),
        truncated=tuple(p for p, f in got.files.items() if f.truncated),
        read={"readme": bool(readme), "docs": bool(kb_docs) or len(in_repo_docs) > 1, "owners": bool(owners),
              "ticket": got.issue is not None},
        dropped=dropped, partial=got.partial)
    return checked(guide, seen)


async def _write(deps: TurnDeps, budget: TurnBudget, description: str, readme: str, prs, seen: str):
    fallback = description or sections.first_paragraph(readme)
    if deps.elapsed() > WRITE_DEADLINE_SECS:
        return fallback, {}, 0
    welcome, lost_a = await sections.welcome(deps, budget, description, readme, seen)
    if deps.elapsed() > WRITE_DEADLINE_SECS:
        return welcome, {}, lost_a
    lessons, lost_b = await sections.lessons(deps, budget, [_pr(p, "") for p in prs], seen)
    return welcome, lessons, lost_a + lost_b


def _owners(paths, rules, prs) -> tuple[dict, ...]:
    owners = [{"who": who, "kind": "owner", "detail": f"CODEOWNERS `{pattern}`"}
              for who, pattern in plan.owners_for(paths, rules)]
    named = {o["who"].lstrip("@").casefold() for o in owners}
    for login, count in plan.top_authors(prs):
        if login.lstrip("@").casefold() in named:
            continue
        noun = "PR" if count == 1 else "PRs"
        owners.append({"who": login, "kind": "recent author",
                       "detail": f"{count} merged {noun} in the last {plan.AUTHOR_WINDOW_DAYS} days"})
    return tuple(owners)


def _pr(pr, lesson: str) -> dict:
    return {"number": pr.number, "title": pr.title, "author": pr.author, "merged": pr.merged_at[:10],
            "link": pr.link, "body": pr.body[:600], "files": list(pr.files), "lesson": lesson}


def checked(guide: Guide, seen: str) -> Guide:
    """The guarantee: a code span the fetched text does not contain is removed, and counted."""
    bad = unverified_spans(render.render(guide), seen)
    if not bad:
        return guide
    log.warning("removing %d unverified code span(s)", len(bad))
    welcome = guide.welcome
    for span in bad:
        welcome = welcome.replace(f"`{span}`", REMOVED)
    return Guide(**{**guide.__dict__, "welcome": welcome, "dropped": guide.dropped + len(bad)})
