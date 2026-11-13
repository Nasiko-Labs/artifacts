"""Pure: verbatim and structure checks on the model's explanation. Nothing the model typed is trusted:

1. every evidence quote must be in the trace or a pasted code block (normalised); failing quotes are removed;
2. fix_code must touch an identifier from the shown code, mention no unknown file, and hold no secret-looking
   string or network/shell call the user did not already use; else it is dropped (prose kept);
3. every file path, `path:line` and `line N` in the prose must be in the trace or code; else the sentence goes;
4. need_code is kept only when it names the candidate file and a range containing its line.
`problems` lists what failed, for the one repair call."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from snippets import CodeView, same_file
from trace_model import Frame, Trace

TEXT_FIELDS = ("likely_cause", "fix_suggestion", "frame_reason", "confidence_notes")
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_PATH = re.compile(r"(?<![\w/])[\w./\\-]*\w\.(?:py|js|mjs|cjs|ts|tsx|jsx|java|kt|go|rs|rb|cs|php|scala|swift)"
                   r"(?::(?P<line>\d+))?\b")
_LINE_REF = re.compile(r"\b(?:line|L)\s?(?P<n>\d{1,6})\b")
_URL = re.compile(r"https?://[^\s)>\]]+")
_HTML = re.compile(r"</?[A-Za-z][^>]*>")
_VIEW_PREFIX = re.compile(r"^\s*L\d+:\s?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_IDENT = re.compile(r"[A-Za-z_][\w]{2,}")
_RISKY = re.compile(r"(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[=:]\s*[\"'][^\"']{6,}[\"']|AKIA[0-9A-Z]{16}|"
                    r"\bsubprocess\b|\bos\.system\b|\bexec\(|\beval\(|child_process|Runtime\.getRuntime|"
                    r"\brequests\.(?:get|post|put|delete)\b|\bfetch\(|\bcurl\b|\bwget\b|https?://")


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").translate(_SMART).split())


@dataclass(frozen=True)
class CheckContext:
    trace: Trace
    views: tuple[CodeView, ...]
    candidate: Frame | None

    @property
    def trace_text(self) -> str:
        return normalise(self.trace.raw)

    @property
    def code_text(self) -> str:
        return normalise("\n".join(line for v in self.views for line in v.lines))

    def known(self, token: str) -> bool:
        needle = normalise(token)
        return bool(needle) and (needle in self.trace_text or needle in self.code_text
                                 or any(needle in normalise(v.path) for v in self.views))

    def line_known(self, number: int, path: str = "") -> bool:
        frames = [f for f in self.trace.all_frames() if not path or same_file(path, f.file)]
        if any(f.line == number for f in frames):
            return True
        return any(v.has_line(number) and (not path or same_file(path, v.path)) for v in self.views)


@dataclass
class Checked:
    result: dict
    problems: list[str] = field(default_factory=list)
    quotes_removed: int = 0
    quotes_kept: int = 0
    code_quotes: int = 0
    sentences_removed: int = 0
    fix_code_dropped: str = ""
    need_code_ignored: bool = False


def run(result: dict, ctx: CheckContext) -> Checked:
    out = Checked(dict(result))
    _evidence(out, ctx)
    _fix_code(out, ctx)
    for name in TEXT_FIELDS:
        out.result[name] = _clean_text(out, ctx, name, out.result.get(name))
    steps = [_clean_text(out, ctx, "how_to_verify", s) for s in out.result.get("how_to_verify") or ()]
    out.result["how_to_verify"] = [s for s in steps if s]
    out.result["need_code"] = _need_code(out, ctx, out.result.get("need_code"))
    return out


def _evidence(out: Checked, ctx: CheckContext) -> None:
    kept = []
    for item in out.result.get("evidence") or ():
        quote = _VIEW_PREFIX.sub("", str(item.get("quote") or "")).strip()
        needle = normalise(quote)
        if needle and needle in ctx.code_text:
            kept.append({"source": "code", "quote": quote})
            out.code_quotes += 1
        elif needle and needle in ctx.trace_text:
            kept.append({"source": "trace", "quote": quote})
        else:
            out.quotes_removed += 1
            out.problems.append(f"evidence quote not found verbatim in the data: {quote[:120]!r}")
    out.quotes_kept = len(kept)
    out.result["evidence"] = kept


def _fix_code(out: Checked, ctx: CheckContext) -> None:
    code = out.result.get("fix_code")
    if not code:
        out.result["fix_code"] = None
        return
    reason = ""
    unknown = [m.group(0) for m in _PATH.finditer(code) if not ctx.known(m.group(0).split(":")[0])]
    risky = [m.group(0) for m in _RISKY.finditer(code) if not ctx.known(m.group(0))]
    if unknown:
        reason = f"fix_code mentions a file not in the data: {unknown[0]}"
    elif risky:
        reason = f"fix_code adds a secret-looking value or a network/shell call: {risky[0]}"
    elif not any(ctx.known(word) for word in _IDENT.findall(code)):
        reason = "fix_code touches no identifier from the trace or code"
    if reason:
        out.fix_code_dropped = reason
        out.problems.append(reason)
        out.result["fix_code"] = None


def _clean_text(out: Checked, ctx: CheckContext, name: str, value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    kept = []
    for sentence in _SENTENCE.split(value.strip()):
        bad = _bad_reference(sentence, ctx)
        if bad:
            out.problems.append(f"{name} cites {bad!r}, which is not in the trace or code")
            out.sentences_removed += 1
            continue
        kept.append(_HTML.sub("", sentence))
    return " ".join(kept) or None


def _bad_reference(sentence: str, ctx: CheckContext) -> str:
    for match in _URL.finditer(sentence):
        if not ctx.known(match.group(0).rstrip(".,")):
            return match.group(0)
    for match in _PATH.finditer(sentence):
        path = match.group(0).split(":")[0]
        if not ctx.known(path) and not any(same_file(path, f.file) for f in ctx.trace.all_frames()):
            return match.group(0)
        if match["line"] and not ctx.line_known(int(match["line"]), path):
            return match.group(0)
    for match in _LINE_REF.finditer(sentence):
        if not ctx.line_known(int(match["n"])):
            return match.group(0)
    return ""


def _need_code(out: Checked, ctx: CheckContext, need) -> dict | None:
    if not need:
        return None
    frame = ctx.candidate
    lines = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(need.get("lines") or "")) if isinstance(need, dict) else None
    ok = (frame is not None and lines is not None and same_file(str(need.get("file") or ""), frame.file)
          and int(lines.group(1)) <= frame.line <= int(lines.group(2)))
    if not ok:
        out.need_code_ignored = True
        return None
    return {"file": frame.file, "lines": f"{int(lines.group(1))}-{int(lines.group(2))}"}


def clean_prose(text: str, ctx: CheckContext) -> tuple[str, int]:
    """Free text (a follow-up answer), line by line, with every prose sentence citing an unknown path, line or URL
    removed. Lines inside a ``` fence are kept as written (a test or a change the user asked for)."""
    out = Checked({})
    kept: list[str] = []
    in_fence = False
    for line in (text or "").splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            kept.append(line)
            continue
        if in_fence or not line.strip():
            kept.append(line)
            continue
        cleaned = _clean_text(out, ctx, "answer", line)
        if cleaned:
            kept.append(cleaned)
    return "\n".join(kept).strip(), out.sentences_removed
