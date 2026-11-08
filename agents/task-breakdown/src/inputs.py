"""Pure: what the user gave this turn, read from the A2A message, and how a new request splits into goal,
spec, granularity, team size and deadline.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions (rules), never goal text and never facts about the user's project.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the earlier plan comes from this agent's own checkpointer).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from fence import strip_markers

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_GOAL_CHARS = 2_000
MAX_SPEC_CHARS = 100_000      # the chat paste limit; above it the rest is not read and the reply says so
CHUNK_THRESHOLD_CHARS = 12_000
MAX_CHUNKS = 4
MIN_GOAL_WORDS = 4
INSTRUCTION_LINE_MAX_CHARS = 200
BANDS = {"coarse": (3, 10), "normal": (10, 25), "fine": (25, 40)}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_COARSE = re.compile(r"high[- ]level|\bcoarse\b|\brough(?:ly)?\b|\boverview\b|\bbig[- ]picture\b", re.IGNORECASE)
_FINE = re.compile(r"\bdetailed\b|fine[- ]grained|\bgranular\b|\bin detail\b|\bstep[- ]by[- ]step\b", re.IGNORECASE)
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10}
_NUM = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
_TEAM = re.compile(rf"\b(?:team of|we are|we're|there are|with|for|by) {_NUM} ?(?:people|persons|engineers|devs|"
                   rf"developers|of us|folks|designers|members)\b|\bteam of {_NUM}\b", re.IGNORECASE)
_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
          r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DAY = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"
_PERIOD = r"(?:week|month|quarter|sprint|year)"
_DEADLINE = re.compile(rf"\b(?:by|before|due|deadline:?|until|no later than)\s+(?:the\s+)?(?:end of\s+)?(?:"
                       rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?|{_MONTH}|"
                       rf"\d{{4}}-\d{{2}}-\d{{2}}|{_DAY}|Q[1-4](?:\s+\d{{4}})?|(?:next|this)\s+{_PERIOD}|{_PERIOD})\b")
_PLAN_ASK = re.compile(r"\b(?:plan|make a .{0,30}plan|break (?:it )?down|tasks?|phases?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, and whether JSON output was asked."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Brief:
    goal: str
    spec: str = ""
    granularity: str = "normal"
    team_size: int | None = None
    deadline: str = ""
    spec_cut: int = 0          # characters beyond MAX_SPEC_CHARS that were not read

    @property
    def source(self) -> str:
        """Everything the user typed for this plan: what assumptions and owners are checked against."""
        return f"{self.goal}\n{self.spec}".strip()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; metadata may ask for JSON."""
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    user = current_message(texts[0]).strip() if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants)


def word_count(text: str) -> int:
    return len((text or "").split())


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def granularity(text: str) -> str:
    if _FINE.search(text or ""):
        return "fine"
    return "coarse" if _COARSE.search(text or "") else "normal"


def team_size(text: str) -> int | None:
    match = _TEAM.search(text or "")
    if not match:
        return None
    raw = next(g for g in match.groups() if g).casefold()
    value = NUMBER_WORDS.get(raw) or int(raw)
    return value if 1 <= value <= 20 else None


def deadline(text: str) -> str:
    """The deadline exactly as typed (never converted to a date)."""
    match = _DEADLINE.search(text or "")
    return match.group(0).strip() if match else ""


def asks_for_plan(text: str) -> bool:
    return bool(_PLAN_ASK.search(text or ""))


def is_vague(brief: Brief) -> bool:
    return word_count(brief.goal) < MIN_GOAL_WORDS and not brief.spec.strip()


def parse_brief(text: str) -> Brief:
    """Goal = the whole message when short; else its first paragraph (plus a short closing instruction
    line); the rest is the spec. Granularity, team size and deadline are read from the whole text."""
    clean = strip_markers(text or "").strip()
    if len(clean) <= MAX_GOAL_CHARS:
        goal, spec = clean, ""
    else:
        head, _, rest = clean.partition("\n\n")
        if len(head) > MAX_GOAL_CHARS:
            head, rest = clean[:MAX_GOAL_CHARS], clean[MAX_GOAL_CHARS:]
        goal, spec = head.strip(), rest.strip()
        last = spec.splitlines()[-1].strip() if spec else ""
        if last and len(last) <= INSTRUCTION_LINE_MAX_CHARS and _PLAN_ASK.search(last):
            goal = f"{goal}\n{last}"
    cut = max(0, len(spec) - MAX_SPEC_CHARS)
    return Brief(goal=goal, spec=spec[:MAX_SPEC_CHARS], granularity=granularity(clean), team_size=team_size(clean),
                 deadline=deadline(clean), spec_cut=cut)


def chunk(spec: str) -> list[str]:
    """A spec over 12,000 chars split by paragraphs (then lines) into at most 4 chunks; nothing is dropped."""
    if len(spec) <= CHUNK_THRESHOLD_CHARS:
        return [spec] if spec.strip() else []
    count = min(MAX_CHUNKS, math.ceil(len(spec) / CHUNK_THRESHOLD_CHARS))
    target = math.ceil(len(spec) / count)
    chunks: list[str] = []
    current = ""
    for unit in _units(spec, target):
        if current and len(current) + len(unit) > target and len(chunks) < count - 1:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{unit}" if current else unit
    return [*chunks, current] if current else chunks


def _units(spec: str, target: int) -> list[str]:
    units: list[str] = []
    for para in re.split(r"\n\s*\n", spec):
        if len(para) <= target:
            units.append(para)
            continue
        for line in para.splitlines():
            units.extend(line[i:i + target] for i in range(0, max(len(line), 1), target))
    return [u for u in units if u.strip()]
