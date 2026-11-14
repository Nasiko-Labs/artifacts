"""Pure: where the draft is in one message, however the user phrased the ask.

- An instruction holding a cue leads the message ("Humanize this: <draft>", "can you make this sound more
  natural? <draft>", "de-AI this <draft>") or ends it ("<draft>\\n\\nmake it sound human").
- A short first paragraph that only sets options ("more casual please") is the request.
- Otherwise the whole message is the draft, unless it reads as a question or a request.
- `verbatim_draft` checks the scope call's has_draft answer: only text copied from the message is ever edited.
"""

from __future__ import annotations

import re

import options
from inputs import MIN_DRAFT_WORDS, word_count

CUE_MAX_START = 60
HEAD_MAX_CHARS = 300
REQUEST_MAX_WORDS = 40
INSTRUCTION_MAX_WORDS = 30     # an instruction before ':' / '?' / a line break is at most this long
DEICTIC_MIN_WORDS = 8          # "de-AI this <draft>": the draft after "this" is at least this long
TRAILING_MAX_WORDS = 25        # a trailing instruction line ("<draft>\n\nmake it sound human")
MIN_CUE_DRAFT_WORDS = 3        # an explicit cue makes a draft this short worth humanizing

_NOUN = r"(?:text|email|e-mail|message|draft|post|paragraph|note|letter|reply|bio|essay|blurb|copy|writing|answer)"
_TARGET = rf"(?:this|it|that|my {_NOUN}|the (?:below|following|above)(?: {_NOUN})?|the {_NOUN})"
_MORE = (r"(?:human(?:-like)?|natural|authentic|conversational|like (?:a (?:real )?(?:person|human)|me|i wrote it|"
         r"a (?:person|human) wrote it))")
_LESS = (r"(?:ai(?:-like|-generated|-ish)?|robotic|stiff|formal|corporate|generic|"
         r"like (?:an? )?(?:ai|bot|robot|chatgpt|gpt|machine))")
_BOT = r"(?:ai|an ai|chatgpt|gpt|a bot|a robot|a machine|an? llm)"
CUE = re.compile(
    r"\b(?:humani[sz](?:e|ing)|de-?ai\b|polish|"
    rf"(?:re-?write|reword|rephrase|edit|fix|clean up|tidy up|tidy|tone down)\s+{_TARGET}\b|"
    rf"(?:clean|tidy) {_TARGET} up\b|"
    rf"make {_TARGET} (?:(?:sound|read|feel|seem) )?(?:a (?:bit|lot|little) |much )?(?:(?:more )?{_MORE}|less {_LESS})|"
    rf"(?:does ?n[o']?t|does not|do ?n[o']?t|won'?t) (?:sound|read) like {_BOT}\b|"
    r"sounds? like a (?:real )?(?:person|human) wrote it|"
    r"(?:remove|strip|take out|get rid of) (?:the |all |any )?(?:ai|a\.i\.)(?: (?:tone|feel|patterns?|vibe))?\b|"
    r"(?:sound|read)s? less like (?:an? )?(?:ai|bot|robot|chatgpt)|less robotic|"
    r"clean up (?:the )?ai|ai[- ](?:tone|patterns?|writing|feel|sound))",
    re.IGNORECASE,
)
# Optional politeness before an instruction ("hey, can you please ...").
_LEAD = re.compile(r"\W*(?:(?:hey|hi|ok|okay)\b[\s,!]*)?(?:please\s+)?(?:(?:can|could|would|will) you\s+)?"
                   r"(?:please\s+)?(?:help me\s+)?", re.IGNORECASE)
_TERMINATOR = re.compile(r"[:?\n]")
_DEIXIS_AFTER = re.compile(rf"\s+{_TARGET}\b[,.!]?", re.IGNORECASE)
_DEIXIS_IN = re.compile(r"\b(?:this|it|that|below|following)\b", re.IGNORECASE)
_REQUEST_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|can you|could you|would you|please|"
                            r"write|translate|summari[sz]e|explain|calculate|compute|tell me|give me|draft|create|"
                            r"generate|make me|list|define)\b", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def split_draft(text: str) -> tuple[str, str]:
    """(request words, draft). An instruction holding a cue ("Humanize this:", "can you make this sound more
    natural? <draft>", "de-AI this <draft>") leads the message, or ends it ("<draft>\n\nmake it sound human");
    a short first paragraph before a long rest is the request when it only sets options; otherwise the whole
    message is the draft, unless it reads as a question or a request."""
    stripped = (text or "").strip()
    split = _leading_split(stripped) or _trailing_split(stripped)
    if split is not None:
        return split
    match = _PARAGRAPH_BREAK.search(stripped)
    if match and match.start() <= HEAD_MAX_CHARS and _is_option_line(stripped[:match.start()]):
        return stripped[:match.start()].strip(), stripped[match.end():].strip()
    if is_request(stripped):
        return stripped, ""
    return "", stripped


def _leading_split(text: str) -> tuple[str, str] | None:
    """A cue near the start: the instruction ends at the first ':' / '?' / line break when that comes soon, else
    right after "this" / "it" / "the below text" ("de-AI this <draft>"), else at the first line's colon or end."""
    cue = CUE.search(text, 0, HEAD_MAX_CHARS)
    if cue is None or cue.start() > CUE_MAX_START:
        return None
    end = _TERMINATOR.search(text, cue.end())
    if end and end.start() <= HEAD_MAX_CHARS and word_count(text[:end.start()]) <= INSTRUCTION_MAX_WORDS:
        return text[:end.start()].strip(), text[end.end():].strip()
    cut = _deictic_cut(text, cue)
    if cut is not None:
        return text[:cut].strip(), text[cut:].strip()
    head, _, rest = text.partition("\n")
    if len(head) > HEAD_MAX_CHARS:
        return None
    colon = head.find(":", cue.end())
    if colon != -1:
        return head[:colon].strip(), f"{head[colon + 1:]}\n{rest}".strip()
    return head.strip(), rest.strip()


def _deictic_cut(text: str, cue: re.Match) -> int | None:
    """Where "<cue> this" ends, when the cue opens the message and a draft-sized rest follows it."""
    if _LEAD.match(text).end() != cue.start():
        return None
    after = _DEIXIS_AFTER.match(text, cue.end())
    if after is not None:
        cut = after.end()
    elif _DEIXIS_IN.search(cue.group()):
        cut = cue.end()
    else:
        return None
    rest = text[cut:].strip()
    if word_count(rest) < DEICTIC_MIN_WORDS or _sets_options(rest):
        return None
    return cut


def _trailing_split(text: str) -> tuple[str, str] | None:
    """An instruction as the last paragraph (or line) after the draft: "<draft>\n\nmake it sound human"."""
    breaks = list(_PARAGRAPH_BREAK.finditer(text)) or list(re.finditer(r"\n", text))
    if not breaks:
        return None
    body, tail = text[:breaks[-1].start()].strip(), text[breaks[-1].end():].strip()
    if not body or word_count(tail) > TRAILING_MAX_WORDS:
        return None
    cue = CUE.match(tail, _LEAD.match(tail).end())
    return (tail, body) if cue else None


def _sets_options(text: str) -> bool:
    parsed = options.parse(text)
    return bool(parsed.changed or parsed.as_json)


def has_draft(request: str, draft: str) -> bool:
    """Enough draft to humanize: MIN_DRAFT_WORDS, or MIN_CUE_DRAFT_WORDS after an explicit cue ("humanize: <one
    sentence>") as long as those words are not just options ("humanize: keep my em dashes")."""
    words = word_count(draft)
    if words >= MIN_DRAFT_WORDS:
        return True
    return words >= MIN_CUE_DRAFT_WORDS and bool(CUE.search(request or "")) and not _sets_options(draft)


def verbatim_draft(message: str, draft: str) -> tuple[str, str] | None:
    """Code check on the scope call's has_draft answer: the draft must be copied from the message (whitespace
    may differ) and be at least MIN_CUE_DRAFT_WORDS long. Returns (the rest of the message as the request, the
    draft exactly as the message has it), else None (treated as needs_draft)."""
    tokens = (draft or "").split() if isinstance(draft, str) else []
    if len(tokens) < MIN_CUE_DRAFT_WORDS:
        return None
    found = re.search(r"\s+".join(re.escape(t) for t in tokens), message or "")
    if found is None:
        return None
    request = f"{message[:found.start()]} {message[found.end():]}".strip()
    return request, found.group()


def _is_option_line(head: str) -> bool:
    """A short first paragraph that only sets options ("more casual please"), not a greeting line."""
    return word_count(head) < MIN_DRAFT_WORDS and _sets_options(head)


def is_request(text: str) -> bool:
    plain = (text or "").strip()
    if word_count(plain) >= REQUEST_MAX_WORDS or "\n" in plain:
        return False
    return plain.endswith("?") or bool(_REQUEST_START.match(plain))
