"""Pure: what the user asked, read in code. A pull request (link, `owner/repo#N`, "PR N in owner/repo",
"PR N" with the repository remembered), options (`strict`, `include existing`, a language restriction,
"as json"), and pasted input: a unified diff, code under `# file: path` headers, or findings (scanner
JSON, SARIF, or this agent's envelope). Plus the actions this agent never takes (merge, approve ...)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import diffmap
import findings_parse

_PR_URL = re.compile(r"https?://[^\s/]+/([\w.-]+)/([\w.-]+)/(?:pulls?|merge_requests|pull-requests)/(\d+)",
                     re.IGNORECASE)
_REPO_HASH = re.compile(r"(?<![\w/.-])([\w.-]+)/([\w.-]+)#(\d+)\b")
_PR_IN_REPO = re.compile(r"\b(?:PR|pull request|merge request|MR)\s*#?\s*(\d+)\s+(?:in|on|of|from)\s+"
                         r"([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*[A-Za-z0-9])", re.IGNORECASE)
_PR_WORD = re.compile(r"\b(?:PR|pull request|merge request|MR)\s*#?\s*(\d+)\b", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w/.:-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*[A-Za-z0-9])(?![\w/#-])")
_FILE_HEADER = re.compile(r"^[ \t]*(?:#|//|--)[ \t]*file:[ \t]*(\S+)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_STRICT = re.compile(r"\bstrict\b", re.IGNORECASE)
_EXISTING = re.compile(r"\binclud\w*\s+(?:the\s+)?existing\b|\bshow\s+(?:the\s+)?existing\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_LANG = re.compile(r"\b(?:only\s+(python|javascript|typescript|go|java)|(python|javascript|typescript|go|java)\s+only)\b",
                   re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"^\s*(?:please\s+|now\s+|then\s+|and\s+|just\s+|ok,?\s+)?(?:merge|approve|close|label|push|block|reject)\b"
    r"|\b(?:merge|approve|close|block|reject)\s+(?:it|this|that|the\s+(?:pr|pull request))\b"
    r"|\brequest(?:ing)?\s+changes\b|\bmark\s+(?:it|the\s+pr)\s+as\s+approved\b",
    re.IGNORECASE)
_CODE_FENCE = re.compile(r"```[\w-]*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Asked:
    repo: str = ""
    number: str = ""
    strict: bool = False
    include_existing: bool = False
    json_mode: bool = False
    language: str = ""
    pasted: str = ""          # diff | code | findings | ""

    @property
    def has_pr(self) -> bool:
        return bool(self.repo and self.number)


def instruction(text: str) -> str:
    """The user's own words before any pasted block (a diff, a file header, JSON or a code fence)."""
    body = text or ""
    cuts = [i for i in (body.find("diff --git "), body.find("\n--- "), body.find("{"), body.find("```"),
                        _first(_FILE_HEADER, body)) if i >= 0]
    return body[:min(cuts)] if cuts else body


def _first(pattern: re.Pattern[str], text: str) -> int:
    m = pattern.search(text)
    return m.start() if m else -1


def pasted_kind(text: str) -> str:
    if findings_parse.pasted_object(text) is not None:
        return "findings"
    if diffmap.looks_like_diff(text):
        return "diff"
    if _FILE_HEADER.search(text or ""):
        return "code"
    return ""


def parse(text: str, remembered_repo: str = "") -> Asked:
    words = instruction(text)
    kind = pasted_kind(text)
    repo, number = ("", "") if kind else pr_ref(words, remembered_repo)
    lang = _LANG.search(words)
    return Asked(repo, number, bool(_STRICT.search(words)), bool(_EXISTING.search(words)), bool(_JSON.search(words)),
                 (lang.group(1) or lang.group(2)).casefold() if lang else "", kind)


def pr_ref(text: str, remembered_repo: str = "") -> tuple[str, str]:
    """(owner/repo, number) or ("", number) when the repository is unknown, or ("", "")."""
    for pattern in (_PR_URL, _REPO_HASH):
        if m := pattern.search(text or ""):
            return f"{m.group(1)}/{m.group(2)}", m.group(3)
    if m := _PR_IN_REPO.search(text or ""):
        return f"{m.group(2)}/{m.group(3)}", m.group(1)
    if m := _PR_WORD.search(text or ""):
        return repo_in(text) or remembered_repo, m.group(1)
    return "", ""


def repo_in(text: str) -> str:
    for m in _REPO.finditer(text or ""):
        owner, repo = m.groups()
        if "." in owner or repo.endswith((".py", ".ts", ".js", ".go", ".java", ".md", ".json")):
            continue
        return f"{owner}/{repo}"
    return ""


def is_forbidden(text: str) -> bool:
    return bool(_FORBIDDEN.search(instruction(text)))


def code_files(text: str) -> dict[str, str]:
    """Pasted code: each `# file: path` header starts a file that runs to the next header."""
    body = text or ""
    heads = list(_FILE_HEADER.finditer(body))
    out: dict[str, str] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        chunk = body[m.end():end].lstrip("\n")
        fenced = _CODE_FENCE.search(chunk)
        content = fenced.group(1) if fenced else chunk.replace("```", "")
        out[m.group(1)] = content if content.endswith("\n") else content + "\n"
    return out


def wants_json(text: str) -> bool:
    return bool(_JSON.search(instruction(text)))
