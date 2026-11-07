"""Pure: a unified diff (git, `git diff`, PR .patch/.diff or `diff -u`) parsed into files -> hunks -> lines
with old and new line numbers, and each file's kind. Hunk bodies are read by the counts in their headers,
so a removed line that itself starts with `---` is never mistaken for a file header."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_GIT_HEADER = re.compile(r"^diff --git (?:\"?a/)?(\S+?)\"? (?:\"?b/)?(\S+?)\"?$")
_HUNK = re.compile(r"^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@+(.*)$")
_OLD = re.compile(r"^--- (?:a/)?(\S+)")
_NEW = re.compile(r"^\+\+\+ (?:b/)?(\S+)")
_DEV_NULL = "/dev/null"

LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "go.sum",
              "gemfile.lock", "composer.lock", "uv.lock", "pipfile.lock"}
MANIFEST_NAMES = {"package.json", "pyproject.toml", "cargo.toml", "go.mod", "gemfile", "pom.xml", "build.gradle",
                  "build.gradle.kts", "setup.py", "setup.cfg", "pipfile", "composer.json"}
SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".kts", ".rb", ".rs",
              ".cs", ".php", ".scala", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".sh", ".ex", ".exs"}
CONFIG_EXT = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".json", ".env", ".properties", ".tf", ".hcl"}
DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
SCHEMA_EXT = {".proto", ".graphql", ".gql", ".avsc"}
_PROMPT = re.compile(r"(\.prompt$|(^|/)system[^/]*\.md$|(^|/)agents\.md$|(^|/)claude\.md$|skill|(^|/)runbook)")
_TEST = re.compile(r"(^|/)(tests?|__tests__|spec|fixtures?)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$")
_GENERATED = re.compile(r"(^|/)(generated|gen|dist|build|vendor|node_modules|third_party)/|\.pb\.go$|_pb2\.py$|"
                        r"\.min\.(js|css)$|\.generated\.")
_MIGRATION = re.compile(r"(^|/)(migrations?|migrate|alembic|db/migrate)/|\.sql$")
_CI = re.compile(r"(^|/)\.github/workflows/|(^|/)\.gitlab-ci\.yml$|(^|/)jenkinsfile$|(^|/)\.circleci/")
_API_SCHEMA = re.compile(r"(^|/)(openapi|swagger)[^/]*\.(ya?ml|json)$|(^|/)schema\.(json|graphql)$")
_CONFIG_NAME = re.compile(r"(^|/)(dockerfile|docker-compose[^/]*|\.env[^/]*|helm/|k8s/|charts/|settings\.py$|"
                          r"config\.\w+$|configs?/)")
_BINARY = re.compile(r"^(Binary files .* differ|GIT binary patch)")


@dataclass(frozen=True)
class Line:
    kind: str                # "+", "-" or " "
    text: str
    old_no: int | None
    new_no: int | None

    @property
    def number(self) -> int | None:
        return self.new_no if self.new_no is not None else self.old_no


@dataclass(frozen=True)
class Hunk:
    old_start: int
    new_start: int
    header: str
    lines: tuple[Line, ...]


@dataclass
class DiffFile:
    path: str
    old_path: str = ""
    status: str = "modified"  # modified | added | deleted | renamed | binary
    hunks: list[Hunk] = field(default_factory=list)
    kind: str = "other"
    chars: int = 0

    def changed(self, kind: str) -> list[Line]:
        return [ln for h in self.hunks for ln in h.lines if ln.kind == kind]


def looks_like_diff(text: str) -> bool:
    return bool(re.search(r"^@@+ -\d", text or "", re.MULTILINE) or re.search(r"^diff --git ", text or "",
                                                                             re.MULTILINE))


def file_kind(path: str) -> str:
    low = path.casefold()
    name = PurePosixPath(low).name
    ext = PurePosixPath(low).suffix
    checks = (("lock", name in LOCK_NAMES), ("generated", bool(_GENERATED.search(low))),
              ("prompt", bool(_PROMPT.search(low))), ("test", bool(_TEST.search(low))),
              ("migration", bool(_MIGRATION.search(low))), ("ci", bool(_CI.search(low))),
              ("api-schema", ext in SCHEMA_EXT or bool(_API_SCHEMA.search(low))),
              ("build", name in MANIFEST_NAMES or name.startswith("requirements") and ext in (".txt", ".in")),
              ("docs", ext in DOC_EXT or low.startswith("docs/")),
              ("config", ext in CONFIG_EXT or bool(_CONFIG_NAME.search(low))), ("source", ext in SOURCE_EXT))
    return next((kind for kind, hit in checks if hit), "other")


def parse(text: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current: DiffFile | None = None
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        git = _GIT_HEADER.match(line)
        if git:
            current = DiffFile(path=git.group(2), old_path=git.group(1))
            files.append(current)
        elif line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            current = _plain_header(current, files, line, lines[i + 1])
            i += 1
        elif current is not None and line.startswith(("new file mode", "deleted file mode", "rename ")):
            _git_meta(current, line)
        elif current is not None and _BINARY.match(line):
            current.status = "binary"
        elif current is not None and _HUNK.match(line):
            hunk, i = _read_hunk(lines, i)
            current.hunks.append(hunk)
            continue
        i += 1
    for f in files:
        f.kind = file_kind(f.path if f.status != "deleted" else f.old_path or f.path)
        f.chars = sum(len(ln.text) + 2 for h in f.hunks for ln in h.lines)
    return files


def _plain_header(current: DiffFile | None, files: list[DiffFile], old: str, new: str) -> DiffFile:
    old_path = _OLD.match(old).group(1)
    new_path = _NEW.match(new).group(1)
    if current is None or current.hunks or (current.path not in (new_path, old_path) and new_path != _DEV_NULL):
        current = DiffFile(path=new_path if new_path != _DEV_NULL else old_path, old_path=old_path)
        files.append(current)
    if old_path == _DEV_NULL:
        current.status = "added"
    elif new_path == _DEV_NULL:
        current.status, current.path = "deleted", old_path
    return current


def _git_meta(current: DiffFile, line: str) -> None:
    if line.startswith("new file mode"):
        current.status = "added"
    elif line.startswith("deleted file mode"):
        current.status = "deleted"
    elif line.startswith("rename from "):
        current.status, current.old_path = "renamed", line[len("rename from "):].strip()
    elif line.startswith("rename to "):
        current.status, current.path = "renamed", line[len("rename to "):].strip()


def _read_hunk(lines: list[str], i: int) -> tuple[Hunk, int]:
    m = _HUNK.match(lines[i])
    old_no, new_no = int(m.group(1)), int(m.group(3))
    old_left = int(m.group(2)) if m.group(2) is not None else 1
    new_left = int(m.group(4)) if m.group(4) is not None else 1
    start_old, start_new = old_no, new_no
    body: list[Line] = []
    i += 1
    while i < len(lines) and (old_left > 0 or new_left > 0):
        raw = lines[i]
        if raw.startswith("\\"):  # "\ No newline at end of file"
            i += 1
            continue
        tag, text = (raw[:1] or " "), raw[1:]
        if tag == "+":
            body.append(Line("+", text, None, new_no))
            new_no, new_left = new_no + 1, new_left - 1
        elif tag == "-":
            body.append(Line("-", text, old_no, None))
            old_no, old_left = old_no + 1, old_left - 1
        elif tag == " ":
            body.append(Line(" ", text, old_no, new_no))
            old_no, new_no, old_left, new_left = old_no + 1, new_no + 1, old_left - 1, new_left - 1
        else:
            break
        i += 1
    return Hunk(start_old, start_new, m.group(5).strip(), tuple(body)), i
