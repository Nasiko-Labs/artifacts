"""Pure: the markdown reply, rendered by code from the envelope. Text is cleaned first: no HTML, no images,
no links, no headings or line breaks that could restyle the reply."""

from __future__ import annotations

import json
import re

_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACES = re.compile(r"\s+")
_LEADING_MARKUP = re.compile(r"^[#>*\-\s]+")
_HEADING = re.compile(r"#{1,6}(?=\s)")
SOURCE_TAG = {"notes": "notes", "updates": "updates", "manager": "your topic", "pasted": "pasted",
              "standing": "standing", "model": ""}


def clean(text: str) -> str:
    plain = _HEADING.sub(" ", _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or ""))))
    return _LEADING_MARKUP.sub("", _SPACES.sub(" ", plain)).replace("**", "").strip()


def _point(n: int, p: dict, sources: bool) -> str:
    lead = "**Your commitment:** " if p.get("flag") == "your commitment" else ""
    extra = f" ({p['overdue']})" if p.get("overdue") else (f" (due {clean(p['due'])})" if p.get("due") else "")
    quote = f' - "{clean(p["quote"])}"' if p["source"] not in ("manager",) and (sources or p.get("status")) else ""
    return f"{n}. {lead}{clean(p['text'])}{extra}{quote} [{SOURCE_TAG.get(p['source'], p['source'])}]"


def _question(n: int, q: dict) -> str:
    tag = "(standing) " if q["source"] == "standing" else ""
    return f"{n}. {tag}{clean(q['text'])}"


def _recognize(r: dict, sources: bool) -> str:
    return f'- {clean(r["text"])} - "{clean(r["quote"])}"' + (f" [{r['source']}]" if sources else "")


def title(env: dict) -> str:
    who = clean(env.get("person") or "") or "your report"
    bits = []
    if env.get("last_1on1"):
        bits.append(f"last 1:1 {clean(env['last_1on1'])}")
    carries = env.get("carry_overs") or []
    if carries:
        open_ = sum(1 for c in carries if c["status"] == "no_evidence")
        bits.append(f"{open_} of {len(carries)} carry-overs have no evidence")
    return f"**1:1 prep: {who}**" + (f" ({'; '.join(bits)})" if bits else "")


def section_points(env: dict, sources: bool = False) -> str:
    lines = [_point(n, p, sources) for n, p in enumerate(env["talking_points"], start=1)]
    return "**Talking points**\n" + ("\n".join(lines) if lines else "_Nothing to carry over or raise._")


def section_done(env: dict) -> str:
    done = [c for c in env.get("carry_overs") or () if c["status"] == "done"]
    if not done:
        return ""
    return "**Done since last time**\n" + "\n".join(
        f'- {clean(c["text"])}: "{clean(c.get("evidence", ""))}" [updates]' for c in done)


def section_questions(env: dict) -> str:
    return "**Questions**\n" + "\n".join(_question(n, q) for n, q in enumerate(env["questions"], start=1))


def section_recognize(env: dict, sources: bool = False) -> str:
    if not env["recognize"]:
        return "**Recognize**\n_Nothing concrete in the updates: ask what they're proud of._"
    return "**Recognize**\n" + "\n".join(_recognize(r, sources) for r in env["recognize"])


def footer(env: dict) -> list[str]:
    left = env.get("left_out") or {}
    lines = []
    if left.get("personal"):
        n = left["personal"]
        lines.append(f"_({n} sentence{'s' if n != 1 else ''} left out as personal.)_")
    if left.get("unverified"):
        n = left["unverified"]
        lines.append(f"_({n} item{'s' if n != 1 else ''} left out: not quoted exactly or not observable.)_")
    lines += [f"_{clean(note)}_" for note in env.get("notes") or ()]
    lines.append(f"_Confidence: {env['confidence']}{'; worth a look before the 1:1' if env['needs_human'] else ''}._")
    return lines


def render(env: dict, view: str = "all") -> str:
    sources = view == "sources"
    if view == "questions":
        return "\n\n".join([title(env), section_questions(env)])
    if view == "recognize":
        return "\n\n".join([title(env), section_recognize(env, True)])
    blocks = [title(env), section_points(env, sources), section_done(env), section_questions(env),
              section_recognize(env, sources), "\n".join(footer(env))]
    return "\n\n".join(b for b in blocks if b)


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"
