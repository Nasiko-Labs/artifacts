"""Pure: which language a file is, whether it is generated or a lockfile, a manifest, or a test path.

A file with no extension (a pasted `snippet`) is detected from its content; when nothing matches, every
rule family runs on it (the patterns are specific enough). Languages with no rule family of their own get
only the generic secret, crypto and config rules, and the review says so."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from diff_parse import CodeFile

GENERIC = "generic"
_BY_SUFFIX = {".py": "py", ".pyw": "py", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js",
              ".tsx": "js", ".vue": "js", ".html": "js", ".htm": "js", ".go": "go", ".java": "java", ".rb": "ruby",
              ".erb": "ruby", ".php": "php", ".cs": "cs", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
              ".sql": "sql", ".yaml": "yaml", ".yml": "yaml", ".json": "config", ".toml": "config", ".ini": "config",
              ".cfg": "config", ".env": "config", ".conf": "config", ".properties": "config", ".tf": "config",
              ".j2": "py", ".jinja": "py", ".jinja2": "py"}
NO_FAMILY = frozenset({".rs", ".c", ".h", ".cpp", ".cc", ".hpp", ".kt", ".kts", ".swift", ".scala", ".ex", ".exs",
                       ".hs", ".clj", ".dart", ".lua", ".pl", ".r", ".m", ".mm", ".groovy"})
MANIFESTS = {"requirements": "pypi", "package.json": "npm", "go.mod": "go", "pom.xml": "maven", "gemfile": "gems",
             "cargo.toml": "crates"}
_LOCKFILE = re.compile(r"(?:^|/)(?:package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|pipfile\.lock|"
                       r"cargo\.lock|go\.sum|gemfile\.lock|composer\.lock)$", re.IGNORECASE)
_GENERATED_PATH = re.compile(r"\.min\.(?:js|css)$|_pb2(?:_grpc)?\.py$|\.pb\.go$|(?:^|/)(?:dist|node_modules|vendor)/",
                             re.IGNORECASE)
_GENERATED_MARK = re.compile(r"@generated|code generated .* do not edit|auto-?generated|do not edit", re.IGNORECASE)
_TEST_PATH = re.compile(r"(?:^|/)(?:tests?|spec|specs|__tests__|fixtures?|examples?|testdata)(?:/|$)|(?:^|/)test_[^/]*$|"
                        r"_test\.(?:py|go)$|\.(?:test|spec)\.[jt]sx?$|Test\.java$", re.IGNORECASE)
_SNIFF = (
    ("php", re.compile(r"<\?php")),
    ("docker", re.compile(r"^FROM \S+", re.MULTILINE)),
    ("go", re.compile(r"^package \w+$|^func (?:\(\w+ \*?\w+\) )?\w+\(", re.MULTILINE)),
    ("java", re.compile(r"\b(?:public|private) (?:static )?(?:class|void|final)\b|System\.out\.")),
    ("py", re.compile(r"^\s*(?:def \w+\(.*\):|from [\w.]+ import |import \w+$|@app\.route)", re.MULTILINE)),
    ("js", re.compile(r"\b(?:const|let) \w+ =|=>|require\(['\"]|function\s*\w*\(|module\.exports")),
    ("ruby", re.compile(r"^\s*(?:def \w+[^:(]*$|end$|require ['\"])", re.MULTILINE)),
)


def name_of(path: str) -> str:
    return PurePosixPath(path).name.casefold()


def language(code: CodeFile) -> str:
    name, suffix = name_of(code.path), PurePosixPath(code.path).suffix.casefold()
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "docker"
    if ".github/workflows/" in code.path.casefold():
        return "yaml"
    if suffix in _BY_SUFFIX:
        return _BY_SUFFIX[suffix]
    if suffix in NO_FAMILY:
        return "none"
    text = "\n".join(ln.text for ln in code.lines[:200])
    return next((lang for lang, rx in _SNIFF if rx.search(text)), GENERIC)


def manifest_kind(code: CodeFile) -> str | None:
    name = name_of(code.path)
    if name.startswith("requirements") and name.endswith((".txt", ".in")):
        return "pypi"
    return MANIFESTS.get(name)


def skip_reason(code: CodeFile) -> str | None:
    """Why a file is not reviewed by rules and triage (secrets are still scanned), or None."""
    if _LOCKFILE.search(code.path):
        return "lockfile"
    head = "\n".join(ln.text for ln in code.lines[:5])
    if _GENERATED_PATH.search(code.path) or _GENERATED_MARK.search(head):
        return "generated file"
    return None


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path or ""))
