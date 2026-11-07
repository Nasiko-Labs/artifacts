"""The follow-up "open the PR for <repo>": re-fetch the repository's exposed manifests on its default
branch (and the branch head), build each verified one-line edit in code, pick the write tools in code and
build the plan. No write happens here; the plan goes to the card walk."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import bump
import manifests
import prompts
import records
import send_guard
from deps import TurnDeps
from discovery import Discoverer
from match import Exposure
from nasiko_mcp import McpTool
from reader import TurnReader
from scan import Scan
from writes import Plan, branch_name

log = logging.getLogger("dependency_risk.pr_prep")

FILE_CALLS, FILE_SECS = 3, 90.0
_FIXABLE = ("affected", "may_resolve_affected")


def fixable(scan: Scan, repo: str) -> list[Exposure]:
    return [r for r in scan.rows if r.repo == repo and r.bump_to and r.status in _FIXABLE]


async def prepare(deps: TurnDeps, scan: Scan, repo: str) -> Plan | str:
    """The plan for the walk, or the reply that ends the turn."""
    rows = fixable(scan, repo)
    if not rows or "/" not in repo:
        return f"{repo} has no declaration I can bump to a fixed version, so there is no upgrade to open."
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return failure
    reader = TurnReader(deps, discoverer)
    base = str(scan.bases.get(repo) or "")
    paths = list(dict.fromkeys(r.manifest for r in rows))
    await reader.run("file", prompts.file_system(repo, base, ", ".join(paths)), "Fetch them.", FILE_CALLS, FILE_SECS)
    texts = [c.text for c in reader.ledger.of("file")]
    base = base or next((b for t in texts if (b := records.default_branch(t))), "")
    if not base:
        return f"I couldn't read the default branch of {repo} from the code host, so I can't prepare a pull request."
    owner, name = repo.split("/", 1)
    target = rows[0].bump_to or ""
    files, problems = _edits(rows, texts, target)
    tools = await _write_tools(discoverer)
    adv = scan.advisory
    return Plan(owner, name, base, next((h for t in texts if (h := records.branch_head(t, base))), ""), adv.package,
                adv.ecosystem, target, adv.id, adv.summary, any(r.major_bump for r in rows), tuple(files),
                tuple(problems), branch_name(adv.package, target), tools=tools)


def _edits(rows: Sequence[Exposure], texts: Sequence[str], target: str) -> tuple[list[dict], list[str]]:
    fetched = {}
    for text in texts:
        for f in records.files(text):
            fetched.setdefault(f.path, f)
    files, problems = [], []
    for path in dict.fromkeys(r.manifest for r in rows):
        row = next(r for r in rows if r.manifest == path)
        file = fetched.get(path)
        if file is None:
            problems.append(f"`{path}`: I couldn't fetch it again, so change `{row.package}` to {target} by hand.")
            continue
        edit = _edit(file.text, row, target)
        if edit is None:
            problems.append(f"`{path}`: the declaration `{row.declared}` can't be edited safely in one line; change "
                            f"`{row.package}` to {target} by hand.")
            continue
        files.append({"path": path, "sha": file.sha, "content": edit.new_content, "diff": edit.diff,
                      "old_line": edit.old_line, "new_line": edit.new_line, "status": row.status,
                      "declared": row.declared})
    return files, problems


def _edit(text: str, row: Exposure, target: str) -> bump.Edit | None:
    found = manifests.find(row.manifest, text, row.package, row.ecosystem)
    dep = next((d for d in found if d.line == row.line), found[0] if found else None)
    return bump.edit(text, dep, target) if dep is not None else None


async def _write_tools(discoverer: Discoverer) -> dict[str, str]:
    prs = (await discoverer.catalog("pr")).write_tools()
    picked = {k: _pick(prs, k) for k in ("branch", "commit", "pr")}
    return {k: t.name for k, t in picked.items() if t is not None}


def _pick(tools: Sequence[McpTool], want: str) -> McpTool | None:
    """The write tool for each card, by the words in its name."""
    def fits(tool: McpTool) -> bool:
        t = set(send_guard.name_tokens(tool.name))
        creates = bool(t & {"create", "open", "add", "new", "post", "put"})
        if t & {"delete", "remove", "merge", "close", "review", "reaction", "comment"}:
            return False
        if want == "branch":
            return bool(t & {"branch", "ref", "reference"}) and creates and not t & {"pull", "file", "update"}
        if want == "commit":
            return bool(t & {"file", "files", "contents", "content"}) and bool(t & {"create", "update", "put"})
        return "pull" in t and creates and not t & {"update"}
    return next((t for t in tools if fits(t)), None)
