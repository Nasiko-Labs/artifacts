"""Pure: which repository paths may ever be written. A path is normalised (relative, no `..`, no absolute
part) and refused when it is a dotfile, under CI or vendored directories, a lockfile, minified or generated.
Config files that decide how tests run are fetched for the test run but never edited."""

from __future__ import annotations

import posixpath
import re

# CI configuration lives in dot-directories, which the dotfile rule already refuses.
FORBIDDEN_DIRS = ("node_modules/", "vendor/", "dist/", "build/")
LOCK_FILES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
                        "cargo.lock", "go.sum", "composer.lock", "gemfile.lock", "uv.lock"})
CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "package.json", "go.mod")
TEST_DIRS = ("tests/", "test/", "__tests__/")
MAX_FILE_BYTES = 200_000

_MINIFIED = re.compile(r"\.min\.[a-z]+$", re.IGNORECASE)
_GENERATED = re.compile(r"_pb2(?:_grpc)?\.py$|\.pb\.go$|\.generated\.|\.g\.dart$", re.IGNORECASE)
_TEST_NAME = re.compile(r"(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.(?:py|go)|[^/]+\.(?:test|spec)\.[jt]sx?)$")


def normalise(path: str) -> str | None:
    """A clean relative path, or None when it is absolute, escapes the root or is empty."""
    raw = (path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    clean = posixpath.normpath(raw)
    if clean in (".", "") or clean.startswith("../") or clean == ".." or "/../" in f"/{clean}/":
        return None
    return clean


def forbidden_reason(path: str) -> str | None:
    """Why this path may never be written, or None when it may."""
    clean = normalise(path)
    if clean is None:
        return "not a safe relative path"
    name = clean.rsplit("/", 1)[-1]
    if any(part.startswith(".") for part in clean.split("/")):
        return "dotfiles and hidden directories are never edited"
    if any(f"/{clean}".startswith(f"/{d}") or f"/{d}" in f"/{clean}" for d in FORBIDDEN_DIRS):
        return "CI, vendored and build directories are never edited"
    if name.casefold() in LOCK_FILES:
        return "lockfiles are never edited"
    if name in CONFIG_FILES:
        return "config that decides how tests run is never edited"
    if _MINIFIED.search(name) or _GENERATED.search(name):
        return "generated or minified files are never edited"
    return None


def is_test_file(path: str) -> bool:
    return bool(_TEST_NAME.search(path)) or any(f"/{d}" in f"/{path}" for d in TEST_DIRS)


def test_dir_of(path: str) -> str | None:
    """The test directory a path sits in (`tests/`, `pkg/__tests__/` ...), or None."""
    parts = path.split("/")[:-1]
    for i, part in enumerate(parts):
        if f"{part}/" in TEST_DIRS:
            return "/".join(parts[:i + 1]) + "/"
    return None
