"""Pure: one pasted message -> the user's instruction, context lines and code files with line numbers.

- Leading prose lines are the instruction ("Security review this diff:"); `Context:` lines before or after
  the code are context. Everything from the first code-like line on is code.
- A unified diff (`@@` hunks or `---`/`+++` headers) becomes one file per `+++` path with the new line number
  for added and context lines and the old line number for removed lines.
- Otherwise `# file: path` (or `// file:`) headers split the paste; code before any header is `snippet` (with
  the extension a ```lang fence names). Markdown fences are dropped. Line numbers count from 1 per file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

PLAIN, ADD, CTX, DEL = "plain", "add", "ctx", "del"
INSTRUCTION_MAX_CHARS = 1_000

_FENCE = re.compile(r"^\s*```\s*([\w+#.-]*)\s*$")
_HEADER = re.compile(r"^\s*(?:#|//|--|;|<!--)?\s*file:\s*(\S+?)\s*(?:-->)?\s*$", re.IGNORECASE)
_CONTEXT = re.compile(r"^\s*context\s*:\s*(.+)$", re.IGNORECASE)
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_CODE_LINE = re.compile(
    r"^\s*(?:def |class |import |from \S+ import|func |package |public |private |protected |static |const |let |"
    r"var |function |return\b|if\s*\(|for\s*\(|while\s*\(|#include|#!|<\?php|<\w+[ >]|@\w+|diff --git|@@ |\+\+\+ |"
    r"--- |FROM |RUN |COPY |ADD |USER |ENV |WORKDIR |CMD |ENTRYPOINT |-r |--[\w-]+|[{}\[\]]|\"[\w@/.-]+\"\s*:|"
    r"[\w.-]+\s*(?:==|>=|<=|~=|!=|===)|gem\s+['\"]|require\b|module\s|SELECT |INSERT |UPDATE |DELETE |CREATE |"
    r"[\w.\[\]'\"]+\s*[:+\-*/]?=\s*\S|[\w.]+\(.*\)\s*[;{]?\s*$|[\w.-]+:\s*$|- \w[\w-]*:|[\w.\-\[\]]*[\d._\-\[][\w.\-\[\]]*$|[+\- ]\s{0,3}\S.*[;(){}=])"
)
_EXTENSIONS = {"python": ".py", "py": ".py", "js": ".js", "javascript": ".js", "jsx": ".jsx", "ts": ".ts",
               "typescript": ".ts", "tsx": ".tsx", "go": ".go", "golang": ".go", "java": ".java", "ruby": ".rb",
               "rb": ".rb", "php": ".php", "csharp": ".cs", "cs": ".cs", "c#": ".cs", "bash": ".sh", "sh": ".sh",
               "shell": ".sh", "zsh": ".sh", "sql": ".sql", "yaml": ".yaml", "yml": ".yaml", "json": ".json",
               "toml": ".toml", "rust": ".rs", "kotlin": ".kt", "c": ".c", "cpp": ".cpp", "vue": ".vue", "html": ".html"}


@dataclass(frozen=True)
class Line:
    no: int
    text: str
    kind: str = PLAIN


@dataclass(frozen=True)
class CodeFile:
    path: str
    lines: tuple[Line, ...]
    is_diff: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "is_diff": self.is_diff, "lines": [[ln.no, ln.text, ln.kind] for ln in self.lines]}

    @staticmethod
    def from_dict(data: dict) -> CodeFile:
        lines = tuple(Line(int(n), str(t), str(k)) for n, t, k in data.get("lines") or ())
        return CodeFile(str(data.get("path") or "snippet"), lines, bool(data.get("is_diff")))

    @property
    def chars(self) -> int:
        return sum(len(ln.text) + 1 for ln in self.lines)


@dataclass(frozen=True)
class Parsed:
    instruction: str
    context: tuple[str, ...]
    files: tuple[CodeFile, ...]


def is_code_line(line: str) -> bool:
    return bool(_CODE_LINE.match(line)) and not line.rstrip().endswith(("?", "."))


def parse(text: str) -> Parsed:
    raw = (text or "").replace("\r\n", "\n").split("\n")
    lead, context, i = [], [], 0
    while i < len(raw):
        line = raw[i]
        if _CONTEXT.match(line):
            context.append(_CONTEXT.match(line).group(1).strip())
        elif line.strip() and (is_code_line(line) or _FENCE.match(line) or _HEADER.match(line)):
            break
        elif line.strip():
            lead.append(line.strip())
        i += 1
    body = raw[i:]
    while body and (not body[-1].strip() or _CONTEXT.match(body[-1])):
        tail = body.pop()
        if _CONTEXT.match(tail):
            context.insert(0, _CONTEXT.match(tail).group(1).strip())
    instruction = " ".join(lead)[:INSTRUCTION_MAX_CHARS]
    files = _diff_files(body) if _is_diff(body) else _plain_files(body)
    return Parsed(instruction, tuple(context), tuple(f for f in files if any(ln.text.strip() for ln in f.lines)))


def _is_diff(lines: list[str]) -> bool:
    if any(_HUNK.match(line) for line in lines):
        return True
    return any(a.startswith("--- ") and b.startswith("+++ ") for a, b in pairwise(lines))


def _diff_path(header: str) -> str:
    path = header[4:].split("\t")[0].strip()
    return path[2:] if path[:2] in ("a/", "b/") else path


def _diff_files(lines: list[str]) -> list[CodeFile]:
    files: list[CodeFile] = []
    path, current, old, new = "diff", [], 1, 1
    for idx, line in enumerate(lines):
        if _FENCE.match(line):
            continue
        if line.startswith("diff --git ") or (line.startswith("--- ") and idx + 1 < len(lines)
                                               and lines[idx + 1].startswith("+++ ")):
            if current:
                files.append(CodeFile(path, tuple(current), True))
                current = []
            if line.startswith("--- "):
                path = _diff_path(line)
            continue
        if line.startswith("+++ "):
            target = _diff_path(line)
            path = path if target == "/dev/null" else target
            continue
        hunk = _HUNK.match(line)
        if hunk:
            old, new = int(hunk.group(1)), int(hunk.group(2))
        elif line.startswith("+"):
            current.append(Line(new, line[1:], ADD))
            new += 1
        elif line.startswith("-"):
            current.append(Line(old, line[1:], DEL))
            old += 1
        elif line.startswith(" ") or line == "":
            current.append(Line(new, line[1:], CTX))
            old, new = old + 1, new + 1
    if current:
        files.append(CodeFile(path, tuple(current), True))
    return files


def _plain_files(lines: list[str]) -> list[CodeFile]:
    files: list[CodeFile] = []
    path, current = "snippet", []
    for line in lines:
        fence, header = _FENCE.match(line), _HEADER.match(line)
        if header:
            files.append(_numbered(path, current))
            path, current = header.group(1), []
        elif fence:
            ext = _EXTENSIONS.get(fence.group(1).casefold(), "")
            if ext and path == "snippet" and not any(s.strip() for s in current):
                path = "snippet" + ext
        else:
            current.append(line)
    files.append(_numbered(path, current))
    return files


def _numbered(path: str, lines: list[str]) -> CodeFile:
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return CodeFile(path, tuple(Line(n, text) for n, text in enumerate(lines, start=1)))
