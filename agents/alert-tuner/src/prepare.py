"""Prepare pull-request plans for chosen `lengthen_for` proposals: read the alert definitions from the
named repository (code host reads only), locate each rule exactly once, build the one-line patch and pick
the three write tools. Nothing is written here; plans go to the approval walk.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import patch as patching
import plan as plans
import prompts
import records
import sender
from alertkey import name_tokens
from deps import TurnDeps
from discovery import Discoverer
from plan import Plan
from reader import TurnReader

log = logging.getLogger("alert_tuner.prepare")

DEFS_CALLS, DEFS_SECS = 5, 90.0
MAX_FILES = 5


@dataclass(frozen=True)
class Prepared:
    plans: tuple[Plan, ...] = ()
    notes: tuple[str, ...] = ()
    copyable: str = ""
    reply: str = ""
    def_notes: tuple[tuple[str, str, bool], ...] = ()   # (alert, why no pull request, ambiguous)


def chosen(report: Mapping, numbers: Sequence[int]) -> tuple[list[tuple[dict, dict]], list[str]]:
    """(alert, lengthen_for proposal) for each named number, and a note per number that has none."""
    picked, notes = [], []
    noisy = report.get("noisy") or []
    for n in numbers:
        if not 1 <= n <= len(noisy):
            notes.append(f"There is no #{n} in the list (it has {len(noisy)}).")
            continue
        alert = noisy[n - 1]
        prop = next((p for p in report["proposals"] if p["alert"] == alert["alert"] and p["kind"] == "lengthen_for"), None)
        if prop is None:
            notes.append(f"#{n} {alert['alert']!r} has no `for` proposal, so there is nothing to change in a pull request.")
            continue
        picked.append((alert, prop))
    return picked, notes


async def prepare(deps: TurnDeps, report: Mapping, numbers: Sequence[int], repo_full: str, hint: str) -> Prepared:
    picked, notes = chosen(report, numbers)
    if not picked:
        return Prepared(notes=tuple(notes))
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Prepared(reply=failure)
    reader = TurnReader(deps, discoverer)
    names = ", ".join(f'"{t}"' for alert, _ in picked for t in name_tokens(alert["alert"])[:2])
    run = await reader.run("defs", prompts.defs_system(repo_full, names, hint, deps.now), "Read them.", DEFS_CALLS,
                           DEFS_SECS)
    if run.status == "no_tools":
        return Prepared(notes=(*notes, run.note), copyable=_copyable(picked))
    texts = [c.text for c in reader.ledger.of("defs")]
    files = _files(texts)
    base = next((b for b in (records.default_branch(t) for t in texts) if b), "")
    base_sha = next((s for s in (records.branch_sha(t, base) for t in texts) if s), "") if base else ""
    if not base or not base_sha:
        why = f"I couldn't read the default branch of {repo_full} and its head commit, so I can't open a pull request."
        return Prepared(notes=(*notes, why), copyable=_copyable(picked))
    tools, missing = await sender.pick_tools(discoverer)
    owner, repo = repo_full.split("/", 1)
    built: list[Plan] = []
    def_notes: list[tuple[str, str, bool]] = []
    for alert, prop in picked:
        made = _plan(alert, prop, files, owner, repo, base, base_sha, repo_full)
        if isinstance(made, str):
            notes.append(made)
            def_notes.append((alert["alert"], made, "more than one rule" in made))
            continue
        built.append(replace(made, tools=tools))
    if built and missing:
        reason = f"I have no granted code-host tool to {missing}."
        return Prepared(notes=tuple(notes), copyable="\n\n".join(plans.copyable(p, reason) for p in built),
                        def_notes=tuple(def_notes))
    return Prepared(tuple(built), tuple(notes), def_notes=tuple(def_notes))


def _files(texts: Sequence[str]) -> list[records.RepoFile]:
    by_path: dict[str, records.RepoFile] = {}
    for text in texts:
        for f in records.files(text):
            by_path.setdefault(f.path, f)
    return list(by_path.values())[:MAX_FILES]


def _plan(alert: Mapping, prop: Mapping, files, owner: str, repo: str, base: str, base_sha: str,
          repo_full: str) -> Plan | str:
    matches = patching.find_rules(files, alert["alert"])
    if not matches:
        return f"definition not found in {repo_full} for {alert['alert']!r}; no pull request."
    if len(matches) > 1:
        where = ", ".join(f"{m.path}:{m.duration_line + 1}" for m in matches[:4])
        return f"{alert['alert']!r} matches more than one rule ({where}); no pull request, change it by hand."
    m = matches[0]
    minutes = int(prop["change"].rsplit(" ", 1)[-1].rstrip("m"))
    source = next(f for f in files if f.path == m.path)
    made = patching.make_patch(source, m, minutes)
    if isinstance(made, str):
        return f"{alert['alert']!r} in {m.path}: {made}; no pull request."
    return plans.build(alert, prop, owner, repo, base, base_sha, made, m.name)


def _copyable(picked) -> str:
    lines = [f"- {alert['alert']}: {prop['change']}" for alert, prop in picked]
    return "Change by hand:\n" + "\n".join(lines)
