"""Pure: command safety classification. Fail closed: anything not on the read-only allowlist changes state.

A command is split into segments (`&&`, `||`, `;`, `|`); the most severe segment decides. Multi-line input is
classified by its first line and flagged for review, as are pipes into a shell, `eval`, backticks and `$(...)`.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

READ_ONLY, CHANGES_STATE, DESTRUCTIVE = "read_only", "changes_state", "destructive"
_SEVERITY = {READ_ONLY: 0, CHANGES_STATE: 1, DESTRUCTIVE: 2}

_DESTRUCTIVE_PATTERNS = (
    r"\brm\s+(?:-\w*\s+)*-\w*(?:rf|fr)\w*", r"\brm\s+-r\s+-f|\brm\s+-f\s+-r", r"\bkubectl\b.*\bdelete\b",
    r"\bdocker\s+(?:rm|rmi|system\s+prune|volume\s+rm|volume\s+prune)\b", r"\bterraform\s+destroy\b",
    r"\baws\b.*\s(?:delete|terminate)-[\w-]+", r"\baws\s+s3\s+(?:rm|rb)\b", r"\bgcloud\b.*\bdelete\b",
    r"\baz\b.*\bdelete\b", r"\bdrop\s+(?:table|database|schema|index|user)\b", r"\btruncate\b",
    r"\bflushall\b|\bflushdb\b", r"\bgit\s+push\b.*(?:--force\b|\s-f\b)", r"^\s*(?:sudo\s+)?shutdown\b",
    r"\bmkfs\b", r"^\s*(?:sudo\s+)?dd\s", r"\bhelm\s+(?:uninstall|delete)\b",
)
_DESTRUCTIVE = re.compile("|".join(_DESTRUCTIVE_PATTERNS), re.IGNORECASE)
_DELETE_FROM = re.compile(r"\bdelete\s+from\b", re.IGNORECASE)
_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)
_REVIEW = re.compile(r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b|\beval\b|`|\$\(", re.IGNORECASE)
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_REDIRECT = re.compile(r"(?<![\d&])>>?\s*(?!/dev/null|&)\S")
_SEGMENTS = re.compile(r"&&|\|\||;|\|")
_SQL_READ = ("select", "explain", "show", "describe", "with")
_SQL_QUERY_FLAG = re.compile(r"""\s-(?:c|e|-command|-execute)\s*=?\s*(["'])(.+?)\1""", re.IGNORECASE)
_VALUE_FLAGS = {"-n", "--namespace", "--context", "--kubeconfig", "-c", "--container", "-l", "--selector", "-h",
                "-p", "-a", "--region", "--profile", "--project", "-o", "--output", "-u", "-U", "-d", "--db"}

_SIMPLE_READ = frozenset({"journalctl", "dig", "nslookup", "host", "ping", "traceroute", "mtr", "ss", "netstat",
                          "ps", "top", "htop", "df", "du", "free", "uptime", "cat", "tail", "head", "grep", "egrep",
                          "zgrep", "less", "more", "wc", "ls", "lsof", "vmstat", "iostat", "whoami", "date", "echo",
                          "jq", "sort", "uniq", "env", "printenv", "hostname", "nproc", "uname", "id", "stat",
                          "find", "watch", "pgrep", "dmesg"})
_SUBCOMMAND_READ = {
    "kubectl": {"get", "describe", "logs", "top", "events", "explain", "version", "api-resources", "cluster-info",
                "rollout status", "rollout history", "auth can-i", "config view", "config get-contexts",
                "config current-context", "diff"},
    "docker": {"ps", "logs", "inspect", "stats", "images", "top", "version", "info", "compose ps", "compose logs"},
    "systemctl": {"status", "is-active", "is-enabled", "show", "list-units", "cat", "is-failed"},
    "helm": {"status", "list", "ls", "history", "get", "show", "template", "search"},
    "terraform": {"plan", "show", "output", "validate", "state list", "state show", "fmt -check", "version"},
    "git": {"log", "status", "diff", "show", "branch", "remote", "fetch", "blame", "rev-parse"},
    "redis-cli": {"info", "get", "ttl", "pttl", "dbsize", "ping", "scan", "type", "exists", "strlen", "hget",
                  "hgetall", "llen", "scard", "zcard", "slowlog get", "client list", "memory usage", "config get",
                  "latency latest", "keys", "mget", "lrange"},
}


@dataclass(frozen=True)
class Classified:
    kind: str
    review: bool = False
    unknown: bool = False


def classify(command: str) -> Classified:
    lines = [line for line in (command or "").splitlines() if line.strip()]
    if not lines:
        return Classified(CHANGES_STATE, unknown=True)
    first = lines[0].strip().removeprefix("$ ").strip()
    review = len(lines) > 1 or bool(_REVIEW.search(first))
    results = [_classify_segment(seg.strip()) for seg in _SEGMENTS.split(_unquoted_masked(first)) if seg.strip()]
    worst = max(results or [Classified(CHANGES_STATE, unknown=True)], key=lambda c: _SEVERITY[c.kind])
    if worst.kind == READ_ONLY and _REDIRECT.search(_QUOTED.sub("''", first)):
        worst = Classified(CHANGES_STATE)  # writes a file
    if _DESTRUCTIVE.search(first) or (_DELETE_FROM.search(first) and not _WHERE.search(first)):
        worst = Classified(DESTRUCTIVE)
    return Classified(worst.kind, review, worst.unknown)


def _unquoted_masked(text: str) -> str:
    """Separators inside quotes (a SQL `;` or a grep `|`) must not split segments: keep quoted runs whole."""
    out, quote = [], ""
    for ch in text:
        if quote:
            out.append("\x00" if ch in "|;&" else ch)
            quote = "" if ch == quote else quote
        else:
            quote = ch if ch in "'\"" else ""
            out.append(ch)
    return "".join(out)


def _classify_segment(segment: str) -> Classified:
    segment = segment.replace("\x00", "|")
    if _DESTRUCTIVE.search(segment):
        return Classified(DESTRUCTIVE)
    words = _words(segment)
    if words and words[0] == "sudo":
        words = words[1:]
    if not words:
        return Classified(CHANGES_STATE, unknown=True)
    tool = words[0].casefold()
    sql = _sql_kind(segment if tool not in ("psql", "mysql") else _embedded_query(segment))
    if sql is not None:
        return sql
    if tool in _SIMPLE_READ:
        return Classified(READ_ONLY)
    if tool == "curl":
        return Classified(READ_ONLY if _curl_is_get(words) else CHANGES_STATE)
    if tool in ("aws", "gcloud", "az"):
        return _cloud(tool, words)
    allowed = _SUBCOMMAND_READ.get(tool)
    if allowed is None:
        return Classified(CHANGES_STATE, unknown=True)
    sub = _positional(words[1:])
    one, two = " ".join(sub[:1]).casefold(), " ".join(sub[:2]).casefold()
    return Classified(READ_ONLY if one in allowed or two in allowed else CHANGES_STATE)


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:  # an unbalanced quote: fall back to whitespace
        return segment.split()


def _positional(words: list[str]) -> list[str]:
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in _VALUE_FLAGS
        else:
            out.append(word)
    return out


def _embedded_query(segment: str) -> str:
    match = _SQL_QUERY_FLAG.search(segment)
    return match.group(2) if match else ""


def _sql_kind(text: str) -> Classified | None:
    first = (text.strip().split() or [""])[0].casefold().rstrip(";")
    if first in _SQL_READ:
        return Classified(READ_ONLY)
    if first in ("update", "insert", "alter", "create", "grant", "revoke", "vacuum", "kill", "set", "delete"):
        return Classified(CHANGES_STATE)
    return None


def _curl_is_get(words: list[str]) -> bool:
    for i, word in enumerate(words):
        if word in ("-X", "--request"):
            method = words[i + 1].upper() if i + 1 < len(words) else ""
            if method not in ("GET", "HEAD"):
                return False
        elif word.startswith("-X") and len(word) > 2 and word[2:].upper() not in ("GET", "HEAD") or word in ("-d", "-F", "-T", "--form", "--upload-file") or word.startswith("--data"):
            return False
    return True


def _cloud(tool: str, words: list[str]) -> Classified:
    lowered = [w.casefold() for w in _positional(words[1:])]
    if tool == "aws":
        if any(w.startswith(("describe-", "list-", "get-")) for w in lowered) or lowered[:2] == ["s3", "ls"]:
            return Classified(READ_ONLY)
        return Classified(CHANGES_STATE)
    if "describe" in lowered or "list" in lowered or "show" in lowered:
        return Classified(READ_ONLY)
    return Classified(CHANGES_STATE)
