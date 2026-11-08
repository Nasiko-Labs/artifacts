"""Pure: split one user message into what this agent reads, in code.

- the user's prose (where the repository, IaC path, ref and options are named; never a paste);
- pasted blocks: a refresh-only (or plan) report, a live object, `=== path ===` IaC blocks, and audit
  events after a line `audit:` (or detected by their keys);
- an envelope from a previous workflow step (`drift_text` / `live`, `repo`, `path`, `ref`, `iac`, `audit`).

Pastes may be fenced (```...```) or not: an unfenced paste starts at the first line that looks like data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

MAX_CHARS = 100_000

_FENCED = re.compile(r"```[\w+-]*[ \t]*\n(.*?)(?:\n```|\Z)", re.DOTALL)
_PLACEHOLDER = "\x00block{}\x00"
_PLACEHOLDER_RE = re.compile(r"^\s*\x00block(\d+)\x00\s*$")
_IAC_HEADER = re.compile(r"^\s*===\s*(\S.*?)\s*===\s*$")
_AUDIT_HEADER = re.compile(r"^audit(?: events| log)?\s*:\s*(.*)$", re.IGNORECASE)
_DATA_LINE = re.compile(
    r"^\s*(?:apiVersion:|kind:|metadata:|spec:|[{\[]|---\s*$|#\s+\S+\s+(?:has|will|must)\b|[~+]\s|-\s|"
    r"resource\s+\"|data\s+\"|Terraform\s|Note:\s+Objects|Objects have changed|[\w.\-]+\s*=\s|\"[\w.\-]+\"\s*:|"
    r"[\w.\-]+:\s*\S|[\w.\-]+:\s*$)")
_PLAN_MARKS = re.compile(r"(?m)^\s*#\s+\S+\s+(?:has changed|has been deleted|will be updated in-place)\s*$|"
                         r"Objects have changed outside of Terraform")
_AUDIT_KEYS = re.compile(r"\"(?:eventName|userIdentity|eventSource)\"\s*:")
_CONFIG_EXT = r"(?:tf|tfvars|hcl|ya?ml|json)"
_PATH = re.compile(rf"(?<![\w/.\-])((?:[\w.\-]+/)*[\w.\-]+\.{_CONFIG_EXT})(?![\w/\-])")
_REPO = re.compile(r"(?<![\w/.\-])([A-Za-z0-9][\w.\-]*)/([A-Za-z0-9][\w.\-]*)(?![\w/\-]|\.\w)")
_REPO_CUE = re.compile(r"\b(?:in|repo|repository|for|from|of|on)\s+`?$", re.IGNORECASE)
_REF = re.compile(r"\b(?:ref|branch)\s+`?([\w./\-]+)`?", re.IGNORECASE)
_UNCHANGED = re.compile(r"\b(?:no (?:code )?changes|unchanged code|code (?:is )?unchanged|nothing changed in (?:the )?code|"
                        r"without (?:any )?code changes)\b", re.IGNORECASE)
_EXTRAS = re.compile(r"\b(?:show|include|with)\s+(?:the\s+)?extras?\b", re.IGNORECASE)
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Inputs:
    prose: str = ""
    repo: str = ""
    path: str = ""
    ref: str = ""
    plan_text: str = ""
    live_text: str = ""
    iac_blocks: tuple[tuple[str, str], ...] = ()
    audit_text: str = ""
    unchanged_code: bool = False
    show_extras: bool = False
    wants_json: bool = False
    too_long: bool = False

    @property
    def has_paste(self) -> bool:
        return bool(self.plan_text or self.live_text or self.iac_blocks)

    @property
    def form(self) -> str:
        """`A` (a drift report), `B` (an IaC file vs a live object), or "" (nothing to compare yet)."""
        if self.plan_text:
            return "A"
        return "B" if self.live_text else ""


def parse(message: str, extra: str = "") -> Inputs:
    """`extra` is an answer to "which IaC file?": its path, repository and pasted blocks are added."""
    text = message or ""
    if len(text) > MAX_CHARS:
        return Inputs(too_long=True)
    found = _envelope(text) or _split(text)
    if extra:
        more = _split(extra)
        found = replace(found, path=more.path or found.path, repo=more.repo or found.repo,
                        ref=more.ref or found.ref, iac_blocks=(*found.iac_blocks, *more.iac_blocks),
                        prose=f"{found.prose}\n{more.prose}".strip())
    return found


def _split(text: str) -> Inputs:
    blocks = [m.group(1) for m in _FENCED.finditer(text)]
    counter = iter(range(len(blocks)))
    rest = _FENCED.sub(lambda _: "\n" + _PLACEHOLDER.format(next(counter)) + "\n", text)
    prose, chunks, iac, audit = [], [], [], []
    mode, current = "prose", []

    def close() -> None:
        if mode == "paste" and "".join(current).strip():
            chunks.append("\n".join(current))
        elif mode.startswith("iac:"):
            iac.append((mode[4:], "\n".join(current)))
        elif mode == "audit":
            audit.append("\n".join(current))

    for line in rest.splitlines():
        placeholder = _PLACEHOLDER_RE.match(line)
        header = _IAC_HEADER.match(line)
        audit_head = _AUDIT_HEADER.match(line)
        if header:
            close()
            mode, current = "iac:" + header.group(1).strip("`"), []
        elif audit_head and (mode == "prose" or not audit_head.group(1) or audit_head.group(1)[:1] in "{["):
            close()
            mode, current = "audit", [audit_head.group(1)]
        elif placeholder:
            body = blocks[int(placeholder.group(1))]
            if mode in ("prose", "paste") or (mode.startswith("iac:") and "".join(current).strip()):
                if mode != "prose":
                    close()  # a fenced block after an IaC block's own text is the next paste
                chunks.append(body)
                mode, current = "prose", []
            else:
                current.append(body)
        elif mode == "prose" and _DATA_LINE.match(line):
            mode, current = "paste", [line]
        elif mode == "prose":
            prose.append(line)
        else:
            current.append(line)
    close()
    return _classify("\n".join(prose).strip(), chunks, tuple(iac), "\n".join(a for a in audit if a.strip()))


def _classify(prose: str, chunks: list[str], iac: tuple[tuple[str, str], ...], audit: str) -> Inputs:
    plan = next((c for c in chunks if _PLAN_MARKS.search(c)), "")
    audits = [c for c in chunks if c is not plan and _AUDIT_KEYS.search(c)]
    lives = [c for c in chunks if c is not plan and c not in audits and c.strip()]
    clean = prose.replace("`", "")
    return Inputs(
        prose=prose, repo=find_repo(clean), path=find_path(clean), ref=_first(_REF, clean),
        plan_text=plan, live_text=lives[0] if lives else "", iac_blocks=iac,
        audit_text="\n".join(x for x in (audit, *audits) if x.strip()),
        unchanged_code=bool(_UNCHANGED.search(clean)), show_extras=bool(_EXTRAS.search(clean)),
        wants_json=bool(_JSON_MODE.search(clean)))


def find_path(text: str) -> str:
    return _first(_PATH, text)


def find_repo(text: str) -> str:
    """`owner/repo` named in prose; one after "in"/"repo"/"for"... wins over a bare one."""
    candidates = []
    for m in _REPO.finditer(text):
        owner, name = m.group(1), m.group(2)
        if owner.replace(".", "").isdigit() or re.fullmatch(rf".*\.{_CONFIG_EXT}", name):
            continue
        cued = bool(_REPO_CUE.search(text[:m.start()]))
        candidates.append((not cued, m.start(), f"{owner}/{name}".rstrip(".")))
    return min(candidates)[2] if candidates else ""


def _first(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(1).rstrip(".,;:") if m else ""


def _envelope(text: str) -> Inputs | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    keys = ("drift_text", "live", "repo", "path", "iac", "audit", "plan")
    if not isinstance(data, dict) or not any(k in data for k in keys):
        return None
    plan = _text(data.get("drift_text") or data.get("plan"))
    iac = data.get("iac")
    blocks = tuple((str(k), _text(v)) for k, v in iac.items()) if isinstance(iac, dict) else (
        ((_text(data.get("path")) or "iac", iac),) if isinstance(iac, str) and iac.strip() else ())
    return Inputs(prose=_text(data.get("request")), repo=_text(data.get("repo")), path=_text(data.get("path")),
                  ref=_text(data.get("ref")), plan_text=plan, live_text=_text(data.get("live")), iac_blocks=blocks,
                  audit_text=_text(data.get("audit")), unchanged_code=bool(data.get("unchanged_code")),
                  show_extras=bool(data.get("show_extras")), wants_json=bool(data.get("as_json")))


def _text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)
