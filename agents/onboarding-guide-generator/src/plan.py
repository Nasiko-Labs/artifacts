"""Pure, code first (PLAN.md "Method"): which files to read, the run commands copied verbatim, the
available tasks, the repo map, where to start on the ticket, who to ask, which pull requests to read
and the first-week checklist. Nothing here calls a model; every value is copied from fetched text."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import codeowners
from records import PullRequest
from verify import parse_day

MAX_COMMAND_LINES = 12
MAX_DOCS_FILES = 6
MAX_START_FILES = 5
MAX_PRS = 5
MAX_AUTHORS = 3
AUTHOR_WINDOW_DAYS = 90
MAP_DEPTH = 2
IGNORED_DIRS = frozenset({"node_modules", ".git", "dist", "target", "vendor", "__pycache__", "build", ".venv",
                          ".idea", ".vscode"})
_ROOT_NAMES = (("readme", "readme"), ("contributing", "contributing"), ("architecture", "architecture"))
_DOC_PRIORITY = ("architecture", "design", "overview", "setup", "development", "testing")
_BUILD_FILES = ("makefile", "justfile", "package.json", "pyproject.toml", ".env.example")
_RUN_HEADING = re.compile(r"install|setup|set up|getting started|run|build|test|development|quickstart|quick start",
                          re.IGNORECASE)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_INLINE = re.compile(r"`([^`\n]+)`")
_COMMAND_START = re.compile(r"^(?:\$\s*)?(?:make|just|npm|npx|pnpm|yarn|bun|pip|pip3|uv|poetry|python|python3|cargo|"
                            r"go|docker|docker-compose|bundle|rake|mvn|gradle|\./gradlew|\./[\w.-]+|git|cp|source|"
                            r"export|cd|bin/[\w.-]+|scripts/[\w./-]+|dotnet|mix|composer|php|rails|nix|tox|nox|"
                            r"pytest|terraform|kubectl|helm|deno)\b")
_TASK_NAMES = frozenset({"dev", "start", "test", "lint", "build"})
_MAKE_TARGET = re.compile(r"^([A-Za-z][\w-]*)\s*:(?!=)")
_JUST_RECIPE = re.compile(r"^@?([A-Za-z][\w-]*)(?:\s+[^:=]*)?:(?!=)")
_BOTS = re.compile(r"\[bot\]$|-bot$|^dependabot|^renovate|^github-actions", re.IGNORECASE)


@dataclass(frozen=True)
class CommandGroup:
    source: str
    heading: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    source: str
    name: str
    definition: str


# --- 1. key files ------------------------------------------------------------------------------------

def key_files(listing: Mapping[str, str]) -> tuple[str, ...]:
    """The files worth reading, by name, from the root, docs/ and .github/ listings (at most 14)."""
    files = sorted(p for p, kind in listing.items() if kind == "file")
    picked: list[str] = []
    for stem, _ in _ROOT_NAMES:
        picked += [p for p in files if "/" not in p and p.casefold().startswith(stem)][:1]
        picked += [p for p in files if p.casefold().startswith(f".github/{stem}")][:1]
    picked += [p for p in files if p in codeowners.LOCATIONS][:1]
    picked += [p for p in files if "/" not in p and p.casefold() in _BUILD_FILES]
    picked += [p for p in files if "/" not in p and p.casefold().startswith("docker-compose")][:1]
    picked += _docs(files)
    return tuple(dict.fromkeys(picked))[:14]


def _docs(files: Sequence[str]) -> list[str]:
    docs = [p for p in files if p.casefold().startswith("docs/") and p.count("/") == 1 and p.endswith(".md")]

    def rank(path: str) -> tuple[int, str]:
        name = path.casefold()
        hits = [i for i, word in enumerate(_DOC_PRIORITY) if word in name]
        return (hits[0] if hits else len(_DOC_PRIORITY), name)

    return sorted(docs, key=rank)[:MAX_DOCS_FILES]


# --- 2. commands -------------------------------------------------------------------------------------

def commands(sources: Mapping[str, str]) -> tuple[CommandGroup, ...]:
    """Command lines under run-like headings, verbatim, grouped by heading, at most 12 lines in all."""
    groups: list[CommandGroup] = []
    budget = MAX_COMMAND_LINES
    for source, text in sources.items():
        for heading, lines in _sections(text):
            if budget <= 0:
                return tuple(groups)
            if not _RUN_HEADING.search(heading):
                continue
            kept = tuple(dict.fromkeys(lines))[:budget]
            if kept:
                groups.append(CommandGroup(source, heading, kept))
                budget -= len(kept)
    return tuple(groups)


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """(heading, command lines) per heading: fenced block lines, plus inline code that reads as a command."""
    out: list[tuple[str, list[str]]] = []
    heading, lines, fence = "", [], None
    for raw in (text or "").splitlines():
        marker = _FENCE.match(raw)
        if marker:
            fence = None if fence == marker.group(1) else (fence or marker.group(1))
            continue
        if fence is not None:
            line = raw.rstrip()
            if line.strip() and not line.lstrip().startswith("#"):
                lines.append(line.strip())
            continue
        head = _HEADING.match(raw)
        if head:
            out.append((heading, lines))
            heading, lines = head.group(2).strip(), []
            continue
        lines += [c.strip() for c in _INLINE.findall(raw) if _COMMAND_START.match(c.strip())]
    out.append((heading, lines))
    return [(h, ls) for h, ls in out if ls]


def tasks(files: Mapping[str, str]) -> tuple[Task, ...]:
    """`make`/`just` targets and package scripts named dev|start|test|lint|build, with their definitions."""
    found: list[Task] = []
    for path, text in files.items():
        name = path.rsplit("/", 1)[-1].casefold()
        if name == "makefile":
            found += _recipes(path, text, _MAKE_TARGET)
        elif name == "justfile":
            found += _recipes(path, text, _JUST_RECIPE)
        elif name == "package.json":
            found += _package_scripts(path, text)
        elif name == "pyproject.toml":
            found += _pyproject_scripts(path, text)
    return tuple(found)


def _recipes(path: str, text: str, pattern: re.Pattern[str]) -> list[Task]:
    lines = (text or "").splitlines()
    found: list[Task] = []
    for i, line in enumerate(lines):
        match = pattern.match(line)
        if not match or match.group(1) not in _TASK_NAMES:
            continue
        body = next((nxt.strip() for nxt in lines[i + 1:i + 4] if nxt.startswith(("\t", "    ")) and nxt.strip()), "")
        found.append(Task(path, match.group(1), body or line.strip()))
    return found


def _package_scripts(path: str, text: str) -> list[Task]:
    try:
        scripts = json.loads(text).get("scripts") or {}
    except (ValueError, AttributeError):
        return []
    return [Task(path, k, v) for k, v in scripts.items() if k in _TASK_NAMES and isinstance(v, str)]


def _pyproject_scripts(path: str, text: str) -> list[Task]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    scripts = {**(data.get("project", {}).get("scripts") or {}),
               **(data.get("tool", {}).get("poetry", {}).get("scripts") or {})}
    return [Task(path, k, str(v)) for k, v in scripts.items()]


# --- 3. repo map -------------------------------------------------------------------------------------

def repo_map(listing: Mapping[str, str], readme: str) -> tuple[tuple[str, str], ...]:
    """(directory, note) for each directory up to depth 2; the note is the README line that names the
    directory, copied, else "(no description found)"."""
    dirs = sorted(p for p, kind in listing.items()
                  if kind == "dir" and p.count("/") < MAP_DEPTH and not set(p.split("/")) & IGNORED_DIRS)
    return tuple((d, _note(d, readme)) for d in dirs)


def _note(directory: str, readme: str) -> str:
    for raw in (readme or "").splitlines():
        line = raw.strip().lstrip("-*| ").strip()
        if (f"`{directory}`" in line or f"`{directory}/`" in line or f"{directory}/ " in line) and len(line) > \
                len(directory) + 4:
            return line[:160]
    return "(no description found)"


# --- 4. ticket area ----------------------------------------------------------------------------------

def rank_paths(hits_by_keyword: Mapping[str, Sequence[str]]) -> tuple[tuple[str, int], ...]:
    """Paths by the number of keywords that found them, then shallower first, then name (top 5)."""
    counts: Counter[str] = Counter()
    for paths in hits_by_keyword.values():
        counts.update(set(paths))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].count("/"), kv[0]))
    return tuple(ranked[:MAX_START_FILES])


# --- 5/6. who to ask, recent PRs -----------------------------------------------------------------------

def is_bot(login: str) -> bool:
    return bool(_BOTS.search(login or ""))


def recent(prs: Sequence[PullRequest], today: date) -> tuple[PullRequest, ...]:
    """Merged within the last 90 days, newest first (dates parsed in code)."""
    since = today - timedelta(days=AUTHOR_WINDOW_DAYS)
    dated = [(parse_day(p.merged_at), p) for p in prs]
    kept = [(d, p) for d, p in dated if d is not None and since <= d <= today]
    return tuple(p for _, p in sorted(kept, key=lambda dp: (dp[0], dp[1].number), reverse=True))


def relevant(prs: Sequence[PullRequest], paths: Sequence[str], keywords: Sequence[str]) -> tuple[PullRequest, ...]:
    """PRs touching a located path (or its directory), else mentioning a keyword; all when nothing is located."""
    if not paths and not keywords:
        return tuple(prs)
    dirs = {p.rsplit("/", 1)[0] for p in paths if "/" in p}

    def touches(pr: PullRequest) -> bool:
        if pr.files:
            return any(f in paths or f.rsplit("/", 1)[0] in dirs for f in pr.files)
        text = f"{pr.title} {pr.body}".casefold()
        return any(k.casefold() in text for k in keywords)

    return tuple(p for p in prs if touches(p))


def top_authors(prs: Sequence[PullRequest]) -> tuple[tuple[str, int], ...]:
    counts = Counter(p.author for p in prs if p.author and not is_bot(p.author))
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:MAX_AUTHORS])


def owners_for(paths: Sequence[str], rules: Sequence[codeowners.Rule]) -> tuple[tuple[str, str], ...]:
    """(owner, the pattern that gave it) for the located paths; for no paths, the catch-all rule's owners."""
    targets = paths or ("README.md",)
    found: dict[str, str] = {}
    for path in targets:
        rule = codeowners.matching_rule(path, rules)
        for owner in rule.owners if rule else ():
            found.setdefault(owner, rule.pattern)
    return tuple(found.items())


# --- 8. checklist ------------------------------------------------------------------------------------

def checklist(repo: str, groups: Sequence[CommandGroup], task_list: Sequence[Task], has_ticket: bool,
              docs: Sequence[str]) -> tuple[str, ...]:
    """Fixed template; an item that names a command uses an extracted one, else it is left out."""
    lines = [line for g in groups for line in g.lines]
    names = {t.name: t for t in task_list}
    clone = next((line for line in lines if line.startswith("git clone")), "")
    run = next((line for line in lines if _COMMAND_START.match(line) and "test" not in line and "clone" not in line), "")
    test = next((line for line in lines if "test" in line), "")
    test_task = names.get("test")
    items = [f"Clone {repo}" + (f": `{clone}`" if clone else ""),
             f"Get it running: `{run}`" if run else "",
             f"Run the tests: `{test}`" if test else
             (f"Run the tests: the `test` task in `{test_task.source}`" if test_task else ""),
             f"Read {', '.join(f'`{d}`' for d in docs[:3])}" if docs else "",
             "Read the start files listed for your first ticket and open a draft PR" if has_ticket else "",
             "Introduce yourself to the people under \"Who to ask\""]
    return tuple(i for i in items if i)

