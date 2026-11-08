"""Pure-ish: which changed files are scannable, and safe placement of a PR's file names in the scan
directory. A name that is absolute, climbs out with `..`, holds control characters or backslashes, or
passes through a symlink is rejected with a reason; nothing is ever written outside the scan root."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

LANGUAGES = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
             ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".java": "Java"}
DEPENDENCY_FILES = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "pipfile",
    "pipfile.lock", "poetry.lock", "pyproject.toml", "uv.lock", "setup.py", "setup.cfg", "go.mod", "go.sum",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile", "cargo.toml", "cargo.lock", "gemfile",
    "gemfile.lock", "composer.json", "composer.lock"})
_REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.(?:txt|in)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")


def language(path: str) -> str:
    return LANGUAGES.get(PurePosixPath(path).suffix.casefold(), "")


def is_dependency_file(path: str) -> bool:
    name = PurePosixPath(path).name.casefold()
    return name in DEPENDENCY_FILES or bool(_REQUIREMENTS.match(name))


def reject_reason(rel: str) -> str:
    """Why this name may not be written into the scan directory, or ""."""
    if not rel or not rel.strip():
        return "empty file name"
    if _CONTROL.search(rel):
        return "unsafe characters in the file name"
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return "absolute path"
    if ".." in PurePosixPath(rel).parts:
        return "path climbs out of the repository (..)"
    return ""


def place(root: Path, rel: str) -> Path | str:
    """The file's path under `root`, or the reason it was rejected. Directories on the way are created;
    a symlink anywhere on the way (or at the target) rejects the name."""
    reason = reject_reason(rel)
    if reason:
        return reason
    target = root.joinpath(*PurePosixPath(rel).parts)
    current = root
    for part in PurePosixPath(rel).parts[:-1]:
        current = current / part
        if current.is_symlink():
            return "symlink in the path"
        current.mkdir(exist_ok=True)
    if target.is_symlink():
        return "symlink in the path"
    real_root = os.path.realpath(root)
    if os.path.commonpath([real_root, os.path.realpath(target)]) != real_root:
        return "path leaves the scan directory"
    return target
