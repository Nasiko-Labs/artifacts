"""Pure rendering, in code: markdown (no raw HTML, no images, links only from tool output), the
follow-up views, and the envelope with `confidence` / `needs_human` computed from checks."""

from __future__ import annotations

import re
from collections.abc import Sequence

import codeowners
from guide import Guide

NO_RUN = "No run instructions found in README or docs."
NO_OWNERS = "No owner information found (no CODEOWNERS rule and no recent merged pull requests matched)."
NOT_LOCATED = "Could not locate the ticket's area in the code; start from the map above."
_UNSAFE = re.compile(r"[<>]")
_IMAGE = re.compile(r"!\[")
_SHORT_COMMANDS = 5


def _safe(text: str) -> str:
    one_line = " ".join(str(text or "").split())
    return _IMAGE.sub("[", _UNSAFE.sub("", one_line))


def _title(text: str) -> str:
    return _safe(text).replace("[", "(").replace("]", ")")


def title(guide: Guide) -> str:
    who = f"Welcome, {_safe(guide.hire)}: " if guide.hire else "Onboarding guide: "
    return f"# {who}{guide.repo or 'pasted README'}"


# --- sections ------------------------------------------------------------------------------------------

def welcome(guide: Guide) -> list[str]:
    return [guide.welcome or "_No description found in the repository._"]


def run_section(guide: Guide, limit: int | None = None) -> list[str]:
    out = ["## Get it running"]
    if guide.no_run_instructions:
        return [*out, NO_RUN]
    shown = 0
    for group in guide.commands:
        lines = list(group["lines"])
        if limit is not None:
            lines = lines[:max(0, limit - shown)]
        if not lines:
            continue
        shown += len(lines)
        heading = f', "{_safe(group["heading"])}"' if group.get("heading") else ""
        source = f"the {group['source']}" if group["source"].startswith("pasted") else f"`{group['source']}`"
        out += [f"From {source}{heading}:", "```", *lines, "```"]
    if guide.tasks and limit is None:
        out.append("Available tasks: " + "; ".join(f"`{t['name']}` in `{t['source']}`: `{t['definition']}`"
                                                    for t in guide.tasks))
    elif not guide.commands:
        out.append("Available tasks: " + ", ".join(f"`{t['name']}` in `{t['source']}`" for t in guide.tasks))
    return out


def map_section(guide: Guide) -> list[str]:
    out = ["## Map of the repo"]
    if not guide.repo_map:
        return [*out, "No directories found in the listing."]
    return [*out, *(f"- `{d['path']}`: {_safe(d['note'])}" for d in guide.repo_map)]


def ticket_section(guide: Guide) -> list[str]:
    if not guide.ticket_asked and guide.ticket is None:
        return []
    ticket = guide.ticket
    head = (f'## Your first ticket: {_safe(ticket["key"])} "{_title(ticket["title"])}"' if ticket
            else f"## Your first ticket: {_safe(guide.ticket_asked)}")
    out = [head]
    if ticket and ticket.get("link"):
        out.append(f"Ticket: {ticket['link']}")
    if guide.start_files:
        out.append("Start in:")
        out += [f"- `{f['path']}` ({f['hits']} keyword{'s' if f['hits'] != 1 else ''} found it)"
                for f in guide.start_files]
    else:
        out.append(NOT_LOCATED)
    if guide.keywords:
        out.append("Searched for: " + ", ".join(f"`{k}`" for k in guide.keywords))
    return out


def owners_section(guide: Guide) -> list[str]:
    out = ["## Who to ask"]
    if not guide.owners:
        return [*out, NO_OWNERS]
    return [*out, *(f"- {_safe(o['who'])}: {o['kind']} ({_safe(o['detail'])})" for o in guide.owners)]


def prs_section(guide: Guide) -> list[str]:
    if not guide.prs:
        return []
    out = ["## Recent PRs worth reading"]
    for pr in guide.prs:
        name = f"#{pr['number']} {_title(pr['title'])}"
        head = f"[{name}]({pr['link']})" if pr.get("link") else name
        by = f" by {_safe(pr['author'])}" if pr.get("author") else ""
        lesson = f": {_safe(pr['lesson'])}" if pr.get("lesson") else ""
        out.append(f"- {head}{by}, merged {pr['merged']}{lesson}")
    return out


def docs_section(guide: Guide) -> list[str]:
    if not guide.docs:
        return []
    out = ["## Docs to read"]
    for doc in guide.docs:
        if doc["where"] == "repo":
            out.append(f"- `{doc['title']}` (in the repo)")
        else:
            out.append(f"- [{_title(doc['title'])}]({doc['link']}) (doc store)")
    return out


def checklist_section(guide: Guide) -> list[str]:
    if not guide.checklist:
        return []
    return ["## First-week checklist", *(f"- [ ] {item}" for item in guide.checklist)]


def coverage_lines(guide: Guide) -> list[str]:
    notes = list(guide.coverage)
    if guide.truncated:
        notes.append("truncated at 24,000 characters: " + ", ".join(f"`{p}` (see the full file at `{p}`)"
                                                                    for p in guide.truncated))
    if guide.dropped:
        notes.append(f"{guide.dropped} unsupported sentence(s) removed by the verification check")
    if guide.partial:
        notes.append("partial: a read step stopped early; the guide shows what was read")
    return ["---", "Coverage: " + "; ".join(notes) + "."] if notes else []


# --- views -------------------------------------------------------------------------------------------------

def render(guide: Guide) -> str:
    parts = [[title(guide)], welcome(guide), run_section(guide), map_section(guide), ticket_section(guide),
             owners_section(guide), prs_section(guide), docs_section(guide), checklist_section(guide),
             coverage_lines(guide)]
    return "\n\n".join("\n".join(p) for p in parts if p)


def render_commands(guide: Guide) -> str:
    return "\n".join(run_section(guide))


def render_short(guide: Guide) -> str:
    first = (guide.welcome.split(". ")[0].rstrip(".") + ".") if guide.welcome else ""
    starts = ", ".join(f"`{f['path']}`" for f in guide.start_files[:3])
    who = ", ".join(_safe(o["who"]) for o in guide.owners[:3])
    lines = [title(guide), first, "\n".join(run_section(guide, limit=_SHORT_COMMANDS)),
             f"**Start in:** {starts}" if starts else "", f"**Ask:** {who}" if who else f"**Ask:** {NO_OWNERS}"]
    return "\n\n".join(line for line in lines if line)


def render_owners(guide: Guide, path: str) -> str:
    rules = tuple(codeowners.Rule(r["pattern"], tuple(r["owners"])) for r in guide.rules)
    rule = codeowners.matching_rule(path, rules)
    out = [f"## Who to ask about `{path}`" if _in_repo(guide, path) else f"## Who to ask about {_safe(path)}"]
    if rule and rule.owners:
        out.append(f"- Owners: {', '.join(rule.owners)} (CODEOWNERS `{rule.pattern}` in `{guide.codeowners_path}`)")
    elif guide.codeowners_path:
        out.append(f"- No CODEOWNERS rule in `{guide.codeowners_path}` covers it.")
    else:
        out.append("- No CODEOWNERS file found.")
    authors = _authors_for(guide.recent_prs, path)
    if authors:
        out.append("- Recent authors: " + ", ".join(f"{_safe(a)} ({n} merged PR{'s' if n != 1 else ''})"
                                                   for a, n in authors))
    elif not (rule and rule.owners):
        out.append(f"- {NO_OWNERS}")
    return "\n".join(out)


def render_focus(guide: Guide, path: str) -> str:
    under = [d for d in guide.repo_map if d["path"] == path or d["path"].startswith(path + "/")]
    starts = [f for f in guide.start_files if f["path"].startswith(path + "/") or f["path"] == path]
    out = [f"## Focus: `{path}`" if under or starts else f"## Focus: {_safe(path)}"]
    out += [f"- `{d['path']}`: {_safe(d['note'])}" for d in under] or ["- Not in the directory map I read."]
    out += [f"- Start in `{f['path']}`" for f in starts]
    return "\n".join(out) + "\n\n" + render_owners(guide, path)


def render_section(guide: Guide, topic: str) -> str:
    word = topic.split()[0].casefold()[:4] if topic.split() else ""
    groups = [g for g in guide.commands if word and word in g["heading"].casefold()]
    lines = [line for g in guide.commands for line in g["lines"] if word and word in line.casefold()]
    tasks = [t for t in guide.tasks if word and word in t["name"].casefold()]
    out = [f"## {_safe(topic).capitalize()}"]
    for g in groups:
        out += [f'From `{g["source"]}`, "{_safe(g["heading"])}":', "```", *g["lines"], "```"]
    if not groups and lines:
        out += ["```", *lines, "```"]
    out += [f"- `{t['name']}` in `{t['source']}`: `{t['definition']}`" for t in tasks]
    if len(out) == 1:
        out.append(f"Nothing about {_safe(topic)} found in the README, docs or task files I read.")
    return "\n".join(out)


def _in_repo(guide: Guide, path: str) -> bool:
    return any(d["path"] == path for d in guide.repo_map) or any(f["path"] == path for f in guide.start_files)


def _authors_for(prs: Sequence[dict], path: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for pr in prs:
        files = pr.get("files") or ()
        if pr.get("author") and any(f == path or f.startswith(path.rstrip("/") + "/") for f in files):
            counts = {**counts, pr["author"]: counts.get(pr["author"], 0) + 1}
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]


# --- envelope ----------------------------------------------------------------------------------------------

def confidence(guide: Guide) -> str:
    read = guide.read
    if guide.mode == "pasted" or not read.get("readme"):
        return "low"
    complete = all(read.get(k) for k in ("readme", "docs", "owners", "ticket"))
    clean = not guide.truncated and not guide.dropped and not guide.partial
    return "high" if complete and clean else "medium"


def needs_human(guide: Guide) -> bool:
    return guide.no_run_instructions or not guide.owners


def sections(guide: Guide) -> list[dict]:
    out = [{"title": "Welcome", "source_refs": [guide.readme_path] if guide.readme_path else []},
           {"title": "Get it running", "source_refs": list(dict.fromkeys(
               [g["source"] for g in guide.commands] + [t["source"] for t in guide.tasks]))},
           {"title": "Map of the repo", "source_refs": [d["path"] for d in guide.repo_map]}]
    if guide.ticket_asked or guide.ticket:
        refs = [guide.ticket["link"]] if guide.ticket and guide.ticket.get("link") else []
        out.append({"title": "Your first ticket", "source_refs": refs + [f["path"] for f in guide.start_files]})
    out.append({"title": "Who to ask", "source_refs": [guide.codeowners_path] if guide.codeowners_path else []})
    if guide.prs:
        out.append({"title": "Recent PRs worth reading", "source_refs": [p["link"] for p in guide.prs if p["link"]]})
    if guide.docs:
        out.append({"title": "Docs to read", "source_refs": [d["link"] or d["title"] for d in guide.docs]})
    if guide.checklist:
        out.append({"title": "First-week checklist", "source_refs": []})
    return out


def envelope(guide: Guide, markdown: str) -> dict:
    items = sections(guide)
    summary = (f"Onboarding guide for {guide.hire or 'a new hire'} in {guide.repo or 'a pasted README'}: "
               f"{len(items)} sections, {sum(len(g['lines']) for g in guide.commands)} run command line(s), "
               f"{len(guide.owners)} people to ask.")
    return {"summary": summary, "items": items, "sections": items, "confidence": confidence(guide),
            "needs_human": needs_human(guide), "guide_markdown": markdown, "repo": guide.repo, "hire": guide.hire,
            "ticket": guide.ticket["key"] if guide.ticket else (guide.ticket_asked or None),
            "coverage": list(guide.coverage)}
