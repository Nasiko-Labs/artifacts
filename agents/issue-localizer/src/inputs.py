"""Pure: what the user gave this turn, read from the A2A message, and how it splits into an issue
reference, a repository, pasted files, hints and the issue text.

- The user's own words are the FIRST text part; with chat history only the text after the last
  "Current message:" marker is used. Later text parts are org policy and agent knowledge: never the request.
- A text-type file part (<= 5 MB) is read as pasted text under a `# file: <name>` label.
- Another step's envelope gives `items[].issue` (or `summary`) as the issue and `items[].repo` as the repository.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_MESSAGE_CHARS = 100_000
MAX_ISSUE_CHARS = 12_000
MAX_FILE_BYTES = 5 * 1024 * 1024
LOG_HEAD_LINES = 40
LOG_TAIL_LINES = 20
TEXT_SUFFIXES = (".txt", ".md", ".json", ".log", ".py", ".js", ".ts", ".tsx", ".go", ".java", ".rb", ".rs", ".cs",
                 ".php", ".c", ".cpp", ".h", ".kt", ".swift", ".sql", ".yml", ".yaml", ".toml")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_NAME = r"[A-Za-z0-9][\w.-]*"
_ISSUE_URL = re.compile(rf"https?://[^\s/]+/({_NAME})/({_NAME})/(?:-/)?issues/(\d+)")
_ISSUE_HASH = re.compile(rf"(?<![\w/.-])({_NAME})/({_NAME})#(\d+)\b")
_ISSUE_IN = re.compile(rf"\bissue\s*#?(\d+)\s+(?:in|of|on|from)\s+({_NAME})/({_NAME})", re.IGNORECASE)
_REPO_LABEL = re.compile(rf"\b(?:repo(?:sitory)?|in|for|on|of)\s*:?\s+`?({_NAME})/({_NAME})(?:@([\w./-]+))?`?",
                         re.IGNORECASE)
_BARE_REPO = re.compile(rf"^\s*`?({_NAME})/({_NAME})(?:@([\w./-]+))?`?\s*$")
_LOOSE_REPO = re.compile(r"(?<![\w/.-])([A-Za-z0-9][\w-]*)/([A-Za-z0-9][\w.-]*)(?![\w/])")
_FILE_EXT = re.compile(r"\.(?:py|js|ts|tsx|jsx|go|java|rb|rs|cs|php|c|cpp|h|kt|swift|sql|ya?ml|json|toml|md|txt)$")
_FILE_HEADER = re.compile(r"^#\s*file:\s*(\S+)\s*$", re.MULTILINE)
_HINT = re.compile(r"\blook (?:in|at|under|inside)\s+`?([\w./-]+)`?", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")
_ISSUE_NUMBER_ONLY = re.compile(r"^\s*#?(\d+)\s*$")


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str
    ref: str | None = None

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class PastedFile:
    path: str
    text: str


@dataclass(frozen=True)
class Request:
    issue_text: str
    repo: RepoRef | None = None
    issue_number: int | None = None
    files: tuple[PastedFile, ...] = ()
    hints: tuple[str, ...] = ()
    from_envelope: bool = False

    @property
    def has_issue(self) -> bool:
        return self.issue_number is not None or len(self.issue_text.split()) >= 3


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts, files, notes = [], [], []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "attached-file.txt"
            text, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [f"# file: {name}\n{text}"] if text is not None else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    if files:
        user = "\n\n".join([user, *files]) if user.strip() else "\n\n".join(files)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user[:2_000]))
    return Incoming(user[:MAX_MESSAGE_CHARS], tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    readable = declared.startswith("text/") or declared == "application/json" or \
        PurePath(name).suffix.casefold() in TEXT_SUFFIXES
    if data is None or not readable:
        return None, f"I can't read {name} ({declared or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it."
    return data.decode("utf-8", errors="replace"), None


def parse_request(text: str) -> Request:
    enveloped = _from_envelope(_JSON_WORDS.sub("", text or ""))
    if enveloped is not None:
        return enveloped
    body, files = split_files(text or "")
    number, repo = _issue_ref(body)
    repo = repo or repo_in(_HINT.sub(" ", body))
    hints = tuple(h.rstrip("/.") for h in _HINT.findall(body))
    issue = _strip_refs(_JSON_WORDS.sub("", body))
    issue = (issue.replace(repo.full, " ") if repo else issue).strip()
    if number is not None and len(issue.split()) < 12:
        issue = ""  # only the instruction around the reference: the issue itself is read from the code host
    return Request(shorten_logs(issue)[:MAX_ISSUE_CHARS], repo, number, files, hints)


def repo_in(text: str) -> RepoRef | None:
    """`owner/repo` named after a word such as "in", "repo:" or "for", or alone on the message; never a file path."""
    bare = _BARE_REPO.match(text or "")
    for match in ([bare] if bare else []) + list(_REPO_LABEL.finditer(text or "")):
        owner, name, ref = match.group(1), match.group(2), match.group(3)
        if not _FILE_EXT.search(name) and "." not in owner:
            return RepoRef(owner, name.rstrip("."), ref)
    return None


def issue_number_only(text: str) -> int | None:
    match = _ISSUE_NUMBER_ONLY.match(text or "")
    return int(match.group(1)) if match else None


def repos_named(text: str, limit: int = 4) -> tuple[str, ...]:
    """`owner/repo`-shaped strings in the text (two segments, no file extension), for Q1's options."""
    found = [f"{m.group(1)}/{m.group(2).rstrip('.')}" for m in _LOOSE_REPO.finditer(text or "")
             if not _FILE_EXT.search(m.group(2)) and "." not in m.group(1)]
    return tuple(dict.fromkeys(found))[:limit]


def split_files(text: str) -> tuple[str, tuple[PastedFile, ...]]:
    """(text before the first `# file:` header, the pasted files)."""
    headers = list(_FILE_HEADER.finditer(text))
    if not headers:
        return text, ()
    files = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        lines = [ln for ln in text[header.end():end].strip("\n").split("\n")]
        while lines and _FENCE.match(lines[0]):
            lines = lines[1:]
        while lines and (_FENCE.match(lines[-1]) or not lines[-1].strip()):
            lines = lines[:-1]
        files.append(PastedFile(header.group(1).strip("`").lstrip("./"), "\n".join(lines)))
    return text[:headers[0].start()], tuple(files)


def shorten_logs(text: str) -> str:
    """A fenced block longer than 60 lines keeps its first 40 and last 20 lines."""
    out, block = [], None
    for line in text.split("\n"):
        if _FENCE.match(line):
            if block is None:
                block = [line]
                continue
            out += _shorten(block[1:], block[0]) + [line]
            block = None
            continue
        (out if block is None else block).append(line)
    if block is not None:
        out += _shorten(block[1:], block[0])
    return "\n".join(out)


def _shorten(lines: list[str], opener: str) -> list[str]:
    if len(lines) <= LOG_HEAD_LINES + LOG_TAIL_LINES:
        return [opener, *lines]
    skipped = len(lines) - LOG_HEAD_LINES - LOG_TAIL_LINES
    return [opener, *lines[:LOG_HEAD_LINES], f"[... {skipped} lines skipped ...]", *lines[-LOG_TAIL_LINES:]]


def _issue_ref(text: str) -> tuple[int | None, RepoRef | None]:
    url = _ISSUE_URL.search(text)
    if url:
        return int(url.group(3)), RepoRef(url.group(1), url.group(2))
    hashed = _ISSUE_HASH.search(text)
    if hashed:
        return int(hashed.group(3)), RepoRef(hashed.group(1), hashed.group(2))
    named = _ISSUE_IN.search(text)
    if named:
        return int(named.group(1)), RepoRef(named.group(2), named.group(3))
    return None, None


def _strip_refs(text: str) -> str:
    for pattern in (_ISSUE_URL, _ISSUE_HASH, _ISSUE_IN):
        text = pattern.sub(" ", text)
    return text


def _from_envelope(text: str) -> Request | None:
    stripped = (text or "").strip()
    fenced = re.match(r"\A```(?:json)?\s*(.*?)\s*```\Z", stripped, re.DOTALL)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    issue = next((i["issue"] for i in items if isinstance(i.get("issue"), str)), None) or data.get("summary")
    if not isinstance(issue, str) or not issue.strip():
        return None
    repo_text = next((i["repo"] for i in items if isinstance(i.get("repo"), str)), "")
    bare = _BARE_REPO.match(repo_text)
    repo = RepoRef(bare.group(1), bare.group(2), bare.group(3)) if bare else None
    number, ref_repo = _issue_ref(issue)
    return Request(shorten_logs(issue)[:MAX_ISSUE_CHARS], repo or ref_repo, number, from_envelope=True)
