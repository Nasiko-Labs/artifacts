"""Pure: which pull request the user means, the ticket they name, and the ticket keys found in the
pull request's own text, all read in code.

A target is a pull request (URL, `owner/repo#N`, "PR N" with the repository from the message or the
one remembered for this conversation) or "the latest PR on branch X". A ticket is a tracker key
(`ABC-123`) or a code-host issue (`#N`); an obvious non-ticket token (`UTF-8`, `SHA-256`) is never one.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields, replace

NO_TICKET = "none"
FOCUSES = ("security", "tests", "performance")

_PR_URL = re.compile(r"https?://[^\s/]+/([\w.-]+)/([\w.-]+)/pulls?/(\d+)", re.IGNORECASE)
_REPO_HASH = re.compile(r"(?<![\w/.-])([\w.-]+)/([\w.-]+)#(\d+)\b")
_PR_WORD = re.compile(r"\b(?:PR|pull request|pull)\s*#?\s*(\d+)\b", re.IGNORECASE)
_BRANCH_PR = re.compile(r"\b(?:latest|last|newest)\s+(?:PR|pull request)\s+(?:on|for|from)\s+(?:the\s+)?"
                        r"(?:branch\s+)?[`'\"]?([\w./-]+?)[`'\"]?(?=[\s,.?!]|$)", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w/.:-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*[A-Za-z0-9])(?![\w/#-])")
_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
_KEY_ANY_CASE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,9}-\d{1,6})(?![0-9])")
_CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+((?:[A-Z][A-Z0-9]{1,9}-\d+)|#\d+)",
                      re.IGNORECASE)
_HASH_ISSUE = re.compile(r"(?<![\w/&])#(\d{1,7})\b")
_TICKET_WORD = re.compile(r"\b(?:ticket|issue)\s+#(\d{1,7})\b", re.IGNORECASE)
_FOCUS = re.compile(r"\bfocus(?:ing|ed)?\s+(?:only\s+)?on\s+(security|tests?|testing|performance|perf)\b"
                    r"|\b(?:only|just)\s+(security|tests?|performance)\b", re.IGNORECASE)
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b", re.IGNORECASE)
# Uppercase tokens shaped like a ticket key that never are one.
_NOT_KEYS = frozenset({"UTF", "SHA", "ISO", "RFC", "CVE", "CWE", "AES", "HTTP", "TLS", "SSL", "GPT", "MD", "RSA",
                       "IPV", "PEP", "ES", "WCAG", "OWASP", "X", "V", "H", "P", "RELEASE", "FEATURE", "FIX",
                       "BUGFIX", "HOTFIX", "VERSION", "BUMP", "DEPS", "CHORE", "PR", "PULL", "ISSUE", "BRANCH",
                       "BUILD", "TEST", "STEP", "PY", "PYTHON", "NODE", "WIN", "ARM", "AMD", "UBUNTU", "PHASE",
                       "PART", "OPTION", "ROUND", "BASE", "COVID", "USD", "EUR", "Q", "FY", "H1", "H2", "W"})


@dataclass(frozen=True)
class Target:
    owner: str = ""
    repo: str = ""
    number: str = ""
    branch: str = ""       # "the latest PR on branch X"
    ticket: str = ""       # override: a key, "#N", or NO_TICKET
    focus: str = ""

    @property
    def repo_full(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else ""

    @property
    def has_repo(self) -> bool:
        return bool(self.owner and self.repo)

    def describe(self) -> str:
        where = self.repo_full or "an unnamed repository"
        if self.number:
            return f"{where}#{self.number}"
        return f"the latest pull request on branch {self.branch} in {where}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: object) -> Target | None:
        if not isinstance(data, dict) or not (data.get("number") or data.get("branch")):
            return None
        return Target(**{f.name: str(data.get(f.name) or "") for f in fields(Target)})


def parse(message: str, remembered_repo: str = "") -> Target | None:
    """The pull request named in the message (repository filled from memory when absent), or None."""
    text = message or ""
    found = _explicit(text)
    if found is None:
        return None
    found = replace(found, ticket=ticket_in(text), focus=focus_in(text))
    if found.has_repo:
        return found
    rest = text.replace(found.branch, " ") if found.branch else text
    owner, repo = split_repo(repo_in(rest) or remembered_repo)
    return replace(found, owner=owner, repo=repo)


def _explicit(text: str) -> Target | None:
    if m := _PR_URL.search(text):
        return Target(m.group(1), m.group(2), number=m.group(3))
    if m := _REPO_HASH.search(text):
        return Target(m.group(1), m.group(2), number=m.group(3))
    if m := _BRANCH_PR.search(text):
        return Target(branch=m.group(1).strip("/."))
    if m := _PR_WORD.search(text):
        return Target(number=m.group(1))
    return None


def repo_in(text: str) -> str:
    """`owner/repo` named in free text (not part of a URL or a path), or ""."""
    for m in _REPO.finditer(text or ""):
        owner, repo = m.groups()
        if "." in owner or repo.endswith((".py", ".ts", ".js", ".go", ".rs", ".java", ".md")):
            continue
        return f"{owner}/{repo}"
    return ""


def split_repo(full: str) -> tuple[str, str]:
    owner, _, repo = (full or "").partition("/")
    return (owner, repo) if owner and repo and "/" not in repo else ("", "")


def with_repo(target: Target, full: str) -> Target:
    owner, repo = split_repo(full)
    return replace(target, owner=owner, repo=repo) if owner else target


def is_key(token: str) -> bool:
    prefix = token.split("-", 1)[0].upper()
    return bool(_KEY.fullmatch(token.upper())) and prefix not in _NOT_KEYS and not prefix.isdigit()


def ticket_in(text: str) -> str:
    """A ticket the user named: a tracker key, or "ticket #N" / "issue #N" for a code-host issue."""
    if m := _TICKET_WORD.search(text or ""):
        return f"#{m.group(1)}"
    scrubbed = _PR_URL.sub(" ", _REPO_HASH.sub(" ", text or ""))
    return next((k for k in _KEY.findall(scrubbed) if is_key(k)), "")


def ticket_from_answer(text: str) -> str:
    """The answer to "which ticket?": a key, `#N`, or NO_TICKET for the diff-only option or no key."""
    if (m := _KEY_ANY_CASE.search(text or "")) and is_key(m.group(1)):
        return m.group(1).upper()
    if m := _HASH_ISSUE.search(text or ""):
        return f"#{m.group(1)}"
    return NO_TICKET


def focus_in(text: str) -> str:
    m = _FOCUS.search(text or "")
    if not m:
        return ""
    word = (m.group(1) or m.group(2) or "").casefold()
    if word.startswith("test"):
        return "tests"
    return "performance" if word.startswith("perf") else word


def wants_json(text: str) -> bool:
    return bool(_JSON_MODE.search(text or ""))


def ticket_keys(title: str, body: str, branch: str, commits: tuple[str, ...]) -> tuple[str, ...]:
    """Ticket references in the pull request's own text, ordered by where they were found: a closing
    keyword in the body first, then the title, the branch name, the rest of the body, the commits."""
    found: list[str] = [_norm(m) for m in _CLOSING.findall(body or "")]
    found += [k for k in _KEY.findall(title or "")]
    found += [m.upper() for m in _KEY_ANY_CASE.findall((branch or "").replace("/", " "))]
    found += [k for k in _KEY.findall(body or "")]
    for message in commits:
        found += [k for k in _KEY.findall(message)]
        found += [_norm(m) for m in _CLOSING.findall(message)]
    return tuple(dict.fromkeys(k for k in found if k.startswith("#") or is_key(k)))


def _norm(ref: str) -> str:
    return ref if ref.startswith("#") else ref.upper()


def envelope_input(text: str) -> dict | None:
    """A previous step's envelope (or any JSON object) carrying `repo`, `pr`, `ticket`."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def from_envelope(data: dict, remembered_repo: str = "") -> Target | None:
    full = str(data.get("repo") or "")
    full = full if _REPO.fullmatch(full) else remembered_repo
    pr = str(data.get("pr") or "").lstrip("#")
    if not pr.isdigit():
        return None
    owner, repo = split_repo(full)
    ticket = str(data.get("ticket") or "")
    ticket = ticket.upper() if is_key(ticket) else (ticket if re.fullmatch(r"#\d+", ticket) else "")
    focus = str(data.get("focus") or "")
    return Target(owner, repo, number=pr, ticket=ticket, focus=focus if focus in FOCUSES else "")


def from_model(data: dict, remembered_repo: str = "") -> Target | None:
    """A target the intake model proposed; every field is re-checked by the same patterns."""
    return from_envelope({"repo": data.get("repo"), "pr": data.get("pr"), "ticket": data.get("ticket"),
                          "focus": data.get("focus")}, remembered_repo)
