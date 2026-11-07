"""Pure: which file names are manifests (and of which ecosystem), parsing one, and finding a package
in it. The per-format parsers live in parse_python.py, parse_native.py and parse_jvm_ruby.py."""

from __future__ import annotations

import re
from collections.abc import Callable

import parse_jvm_ruby
import parse_native
import parse_python
from dependency import Dep, same_package

__all__ = ["Dep", "ecosystem_of", "find", "is_manifest", "parse"]

_REQUIREMENTS = re.compile(r"^requirements[\w.\-]*\.txt$", re.IGNORECASE)
_BY_NAME: dict[str, tuple[str, Callable[[str, str], tuple[Dep, ...]]]] = {
    "pyproject.toml": ("pip", parse_python.pyproject),
    "package.json": ("npm", parse_native.package_json),
    "go.mod": ("go", parse_native.go_mod),
    "cargo.toml": ("cargo", parse_native.cargo_toml),
    "pom.xml": ("maven", parse_jvm_ruby.pom),
    "build.gradle": ("maven", parse_jvm_ruby.gradle),
    "build.gradle.kts": ("maven", parse_jvm_ruby.gradle),
    "gemfile": ("gem", parse_jvm_ruby.gemfile),
}


def _entry(path: str) -> tuple[str, Callable[[str, str], tuple[Dep, ...]]] | None:
    name = path.rsplit("/", 1)[-1]
    if _REQUIREMENTS.match(name):
        return "pip", parse_python.requirements
    return _BY_NAME.get(name.casefold())


def ecosystem_of(path: str) -> str | None:
    entry = _entry(path)
    return entry[0] if entry else None


def is_manifest(path: str, eco: str | None = None) -> bool:
    found = ecosystem_of(path)
    return found is not None and (eco is None or found == eco)


def parse(path: str, text: str) -> tuple[Dep, ...]:
    entry = _entry(path)
    return entry[1](path, text or "") if entry else ()


def find(path: str, text: str, package: str, eco: str) -> tuple[Dep, ...]:
    """Every declaration of `package` in this manifest (the same package may be runtime and dev)."""
    return tuple(d for d in parse(path, text) if d.ecosystem == eco and same_package(eco, d.name, package))
