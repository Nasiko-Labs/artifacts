"""Pure: structural dependency risks in pasted manifests and manifest diffs. No advisory data is used, and
the review always says so. Each result is a finding dict in the same shape as the code findings."""

from __future__ import annotations

import re

import popular_names
from diff_parse import ADD, CTX, DEL, PLAIN, CodeFile
from langs import manifest_kind

NO_ADVISORY = "No vulnerability database was checked."
_PY_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_PY_PIN = re.compile(r"==\s*([\w.+-]+)")
_INDEX = re.compile(r"^\s*(--extra-index-url|--index-url|-i|--trusted-host|--find-links|-f)\b\s*(\S*)")
_DIRECT = re.compile(r"(?:git\+|https?://|file:|github:|gitlab:|bitbucket:|\bgit\s*[:=]|\bpath\s*[:=]|"
                     r"\bgithub\s*:|@\s*(?:git\+|https?://))", re.IGNORECASE)
_JSON_PAIR = re.compile(r"^\s*\"([@\w./-]+)\"\s*:\s*\"([^\"]*)\"")
_JSON_SECTION = re.compile(r"^\s*\"(dependencies|devDependencies|optionalDependencies|peerDependencies|scripts)\"\s*:")
_GEM = re.compile(r"^\s*gem\s+['\"]([\w.-]+)['\"]\s*(.*)$")
_CARGO = re.compile(r"^\s*([\w-]+)\s*=\s*(.+)$")
_HOOKS = frozenset({"preinstall", "install", "postinstall", "prepare"})
_REQ_LIKE = re.compile(r"^\s*(?:#.*|-r\s+\S+|--[\w-]+.*|-[if]\s+\S+|[A-Za-z0-9][\w.\-\[\],]*\s*(?:[=<>!~]=?\s*[\w.*+-]+"
                       r".*)?|git\+\S+)\s*$")


def ecosystem(code: CodeFile) -> str | None:
    kind = manifest_kind(code)
    if kind or not code.path.startswith(("snippet", "diff")):
        return kind
    texts = [ln.text for ln in code.lines if ln.text.strip() and ln.kind != DEL]
    pins = sum(1 for t in texts if re.search(r"==|>=|--(?:extra-)?index-url", t))
    if texts and pins and all(_REQ_LIKE.match(t) for t in texts):
        return "pypi"
    return None


def check(code: CodeFile, eco: str) -> list[dict]:
    lines = [ln for ln in code.lines if ln.kind in (PLAIN, ADD, DEL, CTX)]
    found: list[dict] = []
    section = ""
    removed = {}
    for ln in lines:
        if eco == "npm" and _JSON_SECTION.match(ln.text):
            section = _JSON_SECTION.match(ln.text).group(1)
        name, spec = _entry(eco, ln.text, section)
        if ln.kind == DEL and name:
            removed[name.casefold()] = spec
        if ln.kind not in (PLAIN, ADD) or not (name or _INDEX.match(ln.text)):
            continue
        found += _line_findings(eco, code, ln, name, spec, section, removed)
    return found


def _entry(eco: str, text: str, section: str) -> tuple[str, str]:
    stripped = text.split(" #")[0].strip()
    if not stripped or stripped.startswith(("#", "-")):
        return "", ""
    if eco == "pypi":
        m = _PY_REQ.match(stripped)
        return (m.group(1), m.group(3).strip()) if m and not _DIRECT.match(stripped) else _direct(stripped)
    if eco == "npm":
        m = _JSON_PAIR.match(text)
        return (m.group(1), m.group(2)) if m and section else ("", "")
    if eco == "gems":
        m = _GEM.match(text)
        return (m.group(1), m.group(2)) if m else ("", "")
    if eco == "crates":
        m = _CARGO.match(text)
        return (m.group(1), m.group(2)) if m and m.group(1) not in ("name", "version", "edition", "authors") else ("", "")
    return "", ""


def _direct(text: str) -> tuple[str, str]:
    egg = re.search(r"#egg=([\w.-]+)", text)
    return (egg.group(1) if egg else text[:60]), text


def _line_findings(eco, code, ln, name, spec, section, removed) -> list[dict]:
    loc = (code.path, ln.no)
    index = _INDEX.match(ln.text)
    if index:
        return [_finding("high", loc, ln.text, "DEP-EXTRA-INDEX", f"`{index.group(1)}` adds a package source that "
                         "can serve look-alike packages (dependency confusion).",
                         "Use one trusted index, pin hashes (--require-hashes), and remove extra or plain-http indexes.")]
    if eco == "npm" and section == "scripts":
        if name in _HOOKS:
            sev = "medium" if ln.kind == ADD else "info"
            return [_finding(sev, loc, ln.text, "DEP-INSTALL-HOOK", f"An install-time `{name}` script runs on every "
                             "install, on developer machines and CI.", "Review what the script does; avoid install hooks.")]
        return []
    out = []
    if _DIRECT.search(spec or "") or _DIRECT.match(name or ""):
        out.append(_finding("high", loc, ln.text, "DEP-DIRECT-URL", f"`{name}` is installed from a direct git/URL "
                            "source, outside the registry's integrity checks.",
                            "Publish it to your registry or pin a commit SHA and verify it."))
    elif _unpinned(eco, spec):
        out.append(_finding("medium", loc, ln.text, "DEP-UNPINNED", f"`{name}` is not pinned (`{spec or 'any version'}`), "
                            "so a new release, or a compromised one, installs silently.",
                            "Pin an exact version (and use a lockfile or hashes)."))
    near = typosquat_of(name, eco)
    if near:
        out.append(_finding("medium", loc, ln.text, "DEP-TYPOSQUAT", f"Possible typosquat of `{near}`: `{name}` is one "
                            "or two letters away from a very popular package.", f"Verify the name; you probably meant "
                            f"`{near}`.", confidence="needs_validation"))
    out += _diff_findings(code, ln, loc, name, spec, removed, eco)
    return out


def _diff_findings(code, ln, loc, name, spec, removed, eco) -> list[dict]:
    if not code.is_diff or ln.kind != ADD:
        return []
    old = removed.get(name.casefold())
    if old is None:
        return [_finding("info", loc, ln.text, "DEP-ADDED", f"New dependency `{name}` added; review it.",
                         "Check the package's maintainer, age and download count before merging.")]
    new_v, old_v = _version(spec), _version(old)
    if new_v and old_v and new_v < old_v:
        return [_finding("low", loc, ln.text, "DEP-DOWNGRADE", f"`{name}` is downgraded.",
                         "Confirm the downgrade does not reintroduce a fixed vulnerability.")]
    return []


def _unpinned(eco: str, spec: str) -> bool:
    s = (spec or "").strip().strip(",").strip("'\"").strip()
    if eco == "pypi":
        return not s or ("==" not in s and "~=" not in s and "<" not in s)
    if eco in ("npm", "crates"):
        open_ended = s.startswith(">") and "<" not in s
        return s in ("", "*", "latest", "x") or open_ended or s.startswith("^0.")
    if eco == "gems":
        return not s
    return False


def _version(spec: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+(?:\.\d+)*)", spec or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def typosquat_of(name: str, eco: str) -> str | None:
    known = popular_names.BY_ECOSYSTEM.get(eco) or frozenset()
    norm = (name or "").casefold().replace("_", "-")
    if not norm or norm in known or len(norm) < 4:
        return None
    for popular in sorted(known):
        limit = 2 if len(popular) >= 7 else 1
        if abs(len(popular) - len(norm)) <= limit and 0 < _distance(norm, popular) <= limit:
            return popular
    return None


def _distance(a: str, b: str) -> int:
    """Optimal string alignment distance (a transposition counts as one edit)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _finding(severity, loc, line, rule_id, issue, fix, confidence="confirmed") -> dict:
    path, no = loc
    return {"severity": severity, "location": f"{path}:{no}", "file": path, "line": no, "issue": issue, "fix": fix,
            "fix_code": None, "cwe": "CWE-427" if rule_id == "DEP-EXTRA-INDEX" else "CWE-1357",
            "confidence": confidence, "evidence": line.strip()[:300], "rule": rule_id, "reachable_from_input": "n/a",
            "impact": "Anyone who installs this project (developers, CI and production builds) runs the package's code.",
            "kind": "dependency", "family": "dependencies", "test_file": False}
