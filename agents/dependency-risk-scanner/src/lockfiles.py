"""Pure: the resolved version of a package from a lockfile next to its manifest. Read only to report
what is actually installed; never edited. `package-lock.json` (v1 and v2/v3), `poetry.lock`,
`Cargo.lock`. `go.sum` is ignored (go.mod already names the selected version)."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping

from dependency import same_package

_FOR_MANIFEST = {"package.json": ("package-lock.json",), "pyproject.toml": ("poetry.lock",),
                 "cargo.toml": ("Cargo.lock",)}


def lockfile_for(manifest_path: str) -> tuple[str, ...]:
    """Lockfile names that belong next to this manifest (same folder)."""
    return _FOR_MANIFEST.get(manifest_path.rsplit("/", 1)[-1].casefold(), ())


def resolved(path: str, text: str, package: str, eco: str) -> tuple[str, ...]:
    name = path.rsplit("/", 1)[-1].casefold()
    if name == "package-lock.json":
        return _npm(text, package)
    if name in ("poetry.lock", "cargo.lock"):
        return _toml_packages(text, package, eco)
    return ()


def _npm(text: str, package: str) -> tuple[str, ...]:
    try:
        data = json.loads(text)
    except ValueError:
        return ()
    if not isinstance(data, Mapping):
        return ()
    packages = data.get("packages")
    if isinstance(packages, Mapping):
        entry = packages.get(f"node_modules/{package}")
        if isinstance(entry, Mapping) and isinstance(entry.get("version"), str):
            return (entry["version"],)
    deps = data.get("dependencies")
    entry = deps.get(package) if isinstance(deps, Mapping) else None
    if isinstance(entry, Mapping) and isinstance(entry.get("version"), str):
        return (entry["version"],)
    return ()


def _toml_packages(text: str, package: str, eco: str) -> tuple[str, ...]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ()
    rows = data.get("package")
    found = [str(r.get("version")) for r in (rows if isinstance(rows, list) else ())
             if isinstance(r, Mapping) and r.get("version") and same_package(eco, str(r.get("name") or ""), package)]
    return tuple(dict.fromkeys(found))
