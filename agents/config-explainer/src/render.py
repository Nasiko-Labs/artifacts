"""Pure: the markdown reply (or the envelope as one fenced JSON block in JSON mode). Built by code; no raw
HTML, no images, URLs only as they appear in the config."""

from __future__ import annotations

import json
from datetime import datetime

from analysis import Analysis
from explain import Explained

TITLES = {"cron": "Cron", "regex": "Regex", "iam": "IAM policy", "nginx": "nginx config"}
READ_ONLY = "_Analysis only: nothing was applied or changed._"


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def gap_text(minutes: int) -> str:
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} day{'s' if days != 1 else ''}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _gotcha_lines(envelope: dict) -> list[str]:
    if not envelope["gotchas"]:
        return ["**Gotchas**", "- None found in the checks I run."]
    lines = ["**Gotchas**"]
    for g in envelope["gotchas"]:
        line = f"- **{g['severity'].capitalize()} · {g['id']}** {g['text']}"
        if g.get("so_what"):
            line += f" _{g['so_what']}_"
        lines.append(line)
    return lines


def cron_reply(analysis: Analysis, envelope: dict) -> str:
    facts = analysis.facts
    if analysis.parse_error:
        return f"**Cron** — I couldn't read `{analysis.config}`: {analysis.parse_error}."
    phrase = facts["phrase"].rstrip(".").replace(" or on ", " **or** on ")
    lines = [f"**Cron** `{analysis.config}` — {phrase} ({facts['timezone']})"]
    runs = [datetime.fromisoformat(r).strftime("%a %Y-%m-%d %H:%M") for r in facts.get("next_runs", [])]
    if runs:
        lines.append("Next runs: " + " · ".join(runs))
    elif "next_runs" in facts:
        lines.append("Next runs: none in the next 8 years.")
    if "runs_per_366_days" in facts:
        how = f"How often: {facts['runs_per_366_days']:,} runs in the next 366 days"
        gaps = facts["gaps_minutes"]
        if len(gaps) == 1:
            how += f"; every {gap_text(gaps[0])}"
        elif gaps:
            how += f"; shortest gap {gap_text(gaps[0])}, longest {gap_text(gaps[-1])}"
        how += f"; about {facts['runs_per_week']} per week." if facts["runs_per_week"] >= 1 else "."
        lines.append(how)
    return "\n".join([*lines, "", *_gotcha_lines(envelope)])


def reply(analysis: Analysis, explained: Explained | None, envelope: dict, wants_json: bool) -> str:
    if wants_json:
        return as_json(envelope)
    if analysis.kind == "cron":
        return cron_reply(analysis, envelope)
    title = TITLES[analysis.kind]
    lines = [f"**{title}** — {analysis.summary.split(': ', 1)[-1]}"]
    if analysis.parse_error:
        lines += ["", f"I couldn't fully read it: {analysis.parse_error}."]
    elif explained is None or explained.status != "ok":
        lines += ["", "I couldn't write an explanation this time; the computed facts and gotchas are below."]
    else:
        lines += ["", explained.explanation]
    lines += _checked(analysis, explained)
    lines += ["", *_gotcha_lines(envelope)]
    lines += ["", *(f"_{note}_" for note in analysis.notes), READ_ONLY]
    return "\n".join(lines).strip()


def _checked(analysis: Analysis, explained: Explained | None) -> list[str]:
    lines = [f"- {line}" for line in analysis.checked]
    if analysis.kind == "regex" and not analysis.parse_error:
        examples = explained.examples if explained is not None else None
        if examples and examples.get("should_match"):
            lines.append("- Matches (verified): " + ", ".join(f"`{s}`" for s in examples["should_match"]))
        if examples and examples.get("should_not_match"):
            lines.append("- Doesn't match (verified): " + ", ".join(f"`{s}`" for s in examples["should_not_match"]))
        if explained is not None and explained.dropped_examples:
            lines.append(f"- {explained.dropped_examples} proposed example(s) didn't behave as claimed when run, "
                         "so I dropped them.")
        if lines:
            lines.append("- Engine: Python `re`" + ("" if analysis.facts.get("flavour") == "not stated" else
                                                     f" (flavour: {analysis.facts.get('flavour')})"))
    if explained is not None and explained.removed_sentences:
        lines.append(f"- I removed {explained.removed_sentences} sentence(s) that named something not in your config.")
    return ["", "**Checked for you**", *lines] if lines else []
