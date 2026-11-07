"""Pure: finding the config in a message and deciding its kind (cron, regex, iam, nginx) in code. The user's
words win ("this is a cron", "explain this regex"). Kinds this agent doesn't explain yet are recognised so
they get an honest "not supported yet" reply instead of a guess."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from cron import CronError
from cron import parse as parse_cron
from inputs import MAX_CONFIG_CHARS, from_envelope, strip_json_words

KINDS = ("regex", "cron", "iam", "nginx")
_CUES = {
    "regex": re.compile(r"\b(?:regex(?:es|p)?|regular expressions?|pattern)\b", re.IGNORECASE),
    "cron": re.compile(r"\b(?:cron(?:tab|job)?|schedule)\b", re.IGNORECASE),
    "iam": re.compile(r"\b(?:iam|policy|aws polic(?:y|ies))\b", re.IGNORECASE),
    "nginx": re.compile(r"\bnginx\b", re.IGNORECASE),
}
_UNSUPPORTED = (
    ("a Kubernetes manifest", re.compile(r"^\s*apiVersion:.*$[\s\S]*^\s*kind:", re.MULTILINE)),
    ("Terraform", re.compile(r"^\s*(?:resource|provider|variable|module)\s+\"", re.MULTILINE)),
    ("a GitHub Actions workflow", re.compile(r"^\s*jobs:\s*$[\s\S]*runs-on:", re.MULTILINE)),
    ("docker-compose", re.compile(r"^\s*services:\s*$[\s\S]*^\s+image:", re.MULTILINE)),
    ("a Dockerfile", re.compile(r"^\s*FROM\s+\S+[\s\S]*^\s*(?:RUN|COPY|CMD)\s", re.MULTILINE)),
)
_FENCE = re.compile(r"```([\w-]*)\n(.*?)```", re.DOTALL)
_BACKTICK = re.compile(r"`([^`\n]+)`")
_NGINX_BLOCK = re.compile(r"^[ \t]*(?:http|server|location|upstream|events|map)\b[^\n;{]*\{", re.MULTILINE)
_NGINX_DIRECTIVE = re.compile(r"^[ \t]*(?:listen|server_name|proxy_pass|root|alias|try_files|return|rewrite|"
                              r"add_header|proxy_set_header|ssl_\w+|include|location)\b[^\n]*[;{]\s*$", re.MULTILINE)
_CRON_FIELD = re.compile(r"^(?:[0-9*?/,\-]+|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|SUN|MON|TUE|WED|THU|"
                         r"FRI|SAT)(?:[-,][A-Z]{3})*)$", re.IGNORECASE)
_CRON_SPECIAL = re.compile(r"(?:(?:CRON_TZ|TZ)=\S+\s+)?(?:@(?:reboot|yearly|annually|monthly|weekly|daily|midnight|"
                           r"hourly)\b|cron\([^)]*\))", re.IGNORECASE)
_LITERAL = re.compile(r"(?<!\S)/(?:[^/\s\\]|\\.)+/[gimsuyd]*(?=[\s,.;:]|$)")
_REGEX_META = re.compile(r"[\\^$\[\](){}|+*?]")


@dataclass(frozen=True)
class Found:
    status: str                 # ok | ambiguous | none | unsupported | too_long
    kind: str | None = None
    config: str = ""
    words: str = ""             # the user's text around the config
    certain: bool = False       # the user named the kind, or a strong signal decided it
    unsupported: str = ""


def cue(text: str) -> str | None:
    hits = [kind for kind in ("nginx", "iam", "cron", "regex") if _CUES[kind].search(text or "")]
    return hits[0] if hits else None


def classify(text: str, forced: str | None = None) -> Found:
    message = strip_json_words(text)
    envelope = from_envelope(message)
    if envelope is not None:
        message = envelope
    config = _candidate(message, forced or cue(message))
    if not config:
        return _no_config(message)
    if len(config) > MAX_CONFIG_CHARS:
        return Found("too_long", config=config, words=message)
    words = re.sub(r"```[\w-]*\s*```|`\s*`", " ", message.replace(config, " ", 1)).strip()
    named = forced or cue(words)
    detected = detect(config)
    if named in KINDS and (named == detected or _compatible(named, config)):
        return Found("ok", named, config, words, certain=True)
    if detected:
        return Found("ok", detected, config, words, certain=detected in ("iam", "nginx") or config.startswith("/"))
    return Found("ambiguous", None, config, words)


def _no_config(message: str) -> Found:
    for name, pattern in _UNSUPPORTED:
        if pattern.search(message):
            return Found("unsupported", unsupported=name, words=message)
    return Found("none", words=message)


def _compatible(kind: str, config: str) -> bool:
    if kind == "regex":
        return True
    if kind == "iam":
        return config.lstrip().startswith("{")
    if kind == "nginx":
        return ";" in config or "{" in config
    return bool(_cron_window(config)) or bool(_CRON_SPECIAL.search(config))


def detect(config: str) -> str | None:
    stripped = config.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and ("Statement" in data or "Effect" in data):
            return "iam"
    if _NGINX_BLOCK.search(stripped) or len(_NGINX_DIRECTIVE.findall(stripped)) >= 2:
        return "nginx"
    if _CRON_SPECIAL.fullmatch(stripped) or _is_cron(stripped):
        return "cron"
    if _LITERAL.fullmatch(stripped) or len(_REGEX_META.findall(stripped)) >= 2:
        return "regex"
    return None


def _is_cron(text: str) -> bool:
    try:
        spec = parse_cron(text)
    except CronError:
        return False
    return not spec.quartz or len(text.split()) in (6, 7) and all(_CRON_FIELD.match(t) for t in text.split())


def _candidate(message: str, named: str | None) -> str:
    fenced = _FENCE.search(message)
    if fenced:
        return fenced.group(2).strip()
    json_text = _json_object(message)
    if json_text:
        return json_text
    block = _NGINX_BLOCK.search(message) or _NGINX_DIRECTIVE.search(message)
    if block:
        return message[block.start():message.rfind("}") + 1 or len(message)].strip() or message[block.start():]
    ticks = _BACKTICK.findall(message)
    if ticks:
        return max(ticks, key=len).strip()
    special = _CRON_SPECIAL.search(message)
    if special:
        return special.group(0).strip()
    literal = _LITERAL.search(message)
    if literal:
        return literal.group(0)
    if named == "regex":
        return _regex_token(message)
    return _cron_window(message)


def _json_object(message: str) -> str:
    start, end = message.find("{"), message.rfind("}")
    if start == -1 or end <= start:
        return ""
    try:
        json.loads(message[start:end + 1])
    except ValueError:
        return ""
    return message[start:end + 1]


def _cron_window(message: str) -> str:
    tokens = message.split()
    for i in range(len(tokens) - 4):
        window = tokens[i:i + 5]
        if all(_CRON_FIELD.match(t) and not t.endswith(",") and re.search(r"[0-9*]|[A-Za-z]{3}", t) for t in window):
            prefix = tokens[i - 1] + " " if i and re.match(r"(?:CRON_TZ|TZ)=", tokens[i - 1]) else ""
            return prefix + " ".join(window)
    return ""


def _regex_token(message: str) -> str:
    tokens = [t.rstrip(",;") for t in message.split()]
    scored = [t for t in tokens if len(_REGEX_META.findall(t)) >= 2]
    return max(scored, key=len) if scored else ""
