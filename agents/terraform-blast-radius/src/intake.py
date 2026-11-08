"""Pure: what the user gave this turn, read from the A2A message.

- The user's words are the FIRST text part only (later text parts are org policy / knowledge: never data).
  With chat history only the text after the last `Current message:` marker is used.
- A text or JSON file part (inline bytes, <= 5 MB) is read as plan text.
- Envelope input (another step's JSON): `plan_text` or `resource_changes`, plus `repo` and `env`.
- The words around the pasted plan are the request: repositories (`owner/repo`, the first is the IaC
  repository), the environment, and any pasted `path:line:text` search output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import plan_json
import plan_parse
import prompts
from affected import PASTED, environment
from records import Hit

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REPOS = 6
TEXT_TYPES = ("text/plain", "text/markdown", "application/json", "text/x-terraform", "application/octet-stream")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w./:@-])([A-Za-z0-9][\w-]*/[A-Za-z0-9][\w.-]*[A-Za-z0-9])(?![\w/])")
_NOT_REPO_DIRS = {"modules", "module", "stacks", "stack", "environments", "envs", "env", "apps", "src", "terraform",
                  "infra", "live", "charts", "k8s", "deploy", "config", "services", "and", "or"}
_FILE_EXT = re.compile(r"\.(?:tf|tfvars|ya?ml|json|hcl|md|py|txt|sh|env|toml|ini|cfg)$", re.IGNORECASE)
_ENV_WORDS = (("prod", re.compile(r"\b(?:prod|production|prd)\b", re.IGNORECASE)),
              ("staging", re.compile(r"\bstag(?:e|ing)\b", re.IGNORECASE)),
              ("dev", re.compile(r"\b(?:dev|development)\b", re.IGNORECASE)),
              ("qa", re.compile(r"\bqa\b", re.IGNORECASE)))
_GREP = re.compile(r"^\s*(?P<path>[\w./-]+\.[\w]+):(?P<line>\d+):(?P<text>.*)$")
_PLAN_START = re.compile(r"Terraform will perform|Terraform used the selected|An execution plan has been generated|"
                         r"Resource actions are indicated|^\s*# \S+\.\S+ (?:will|must|is|has) ", re.MULTILINE)
_PLAN_END = re.compile(r"^\s*(?:Plan: .*|No changes\..*)$", re.MULTILINE)


@dataclass(frozen=True)
class Incoming:
    text: str
    files: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    plan_text: str = ""
    instruction: str = ""
    repos: tuple[str, ...] = ()
    env: str = ""
    grep: tuple[Hit, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts, files = [], []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "raw" and _readable(part.media_type, part.filename, len(part.raw)):
            files.append(part.raw.decode("utf-8", errors="replace"))
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(files), wants)


def _readable(media_type: str, filename: str, size: int) -> bool:
    kind = (media_type or "").split(";")[0].strip().casefold()
    named = (filename or "").casefold().endswith((".txt", ".json", ".tfplan.txt", ".plan", ".log", ".out"))
    return size <= MAX_FILE_BYTES and (kind in TEXT_TYPES or named)


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def split(incoming: Incoming) -> Request:
    text = prompts.strip_markers(incoming.text)
    envelope = _envelope(text)
    if envelope is not None:
        return envelope
    for file_text in incoming.files:
        if plan_parse.looks_like_plan(file_text):
            return _request(file_text, text)
    span = plan_json.object_span(text)
    if span is not None:
        return _request(text[span[0]:span[1]], text[:span[0]] + "\n" + text[span[1]:])
    start = _PLAN_START.search(text)
    if start is None:
        return _request("", text)
    ends = list(_PLAN_END.finditer(text, start.start()))
    stop = ends[-1].end() if ends else len(text)
    return _request(text[start.start():stop], text[:start.start()] + "\n" + text[stop:])


def _request(plan_text: str, instruction: str) -> Request:
    return Request(plan_text, instruction.strip(), repos_in(instruction), env_in(instruction), grep_in(instruction))


def _envelope(text: str) -> Request | None:
    body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not ("plan_text" in data or "resource_changes" in data):
        return None
    plan_text = str(data.get("plan_text") or "") or json.dumps({"resource_changes": data["resource_changes"]})
    repos = [str(data.get("repo") or ""), *(str(r) for r in data.get("also_search") or ())]
    return Request(plan_text, "", tuple(r for r in repos if _REPO.fullmatch(r)), _env_word(str(data.get("env") or "")))


def repos_in(text: str) -> tuple[str, ...]:
    found = []
    for match in _REPO.finditer(text or ""):
        repo = match.group(1)
        first = repo.split("/", 1)[0].casefold()
        if first in _NOT_REPO_DIRS or _FILE_EXT.search(repo) or _grep_line(text, match.start()):
            continue
        found.append(repo)
    return tuple(dict.fromkeys(found))[:MAX_REPOS]


def _grep_line(text: str, pos: int) -> bool:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return bool(_GREP.match(text[start:end if end != -1 else len(text)]))


def env_in(text: str) -> str:
    return _env_word(text)


def _env_word(text: str) -> str:
    return next((env for env, rule in _ENV_WORDS if rule.search(text or "")), "")


def grep_in(text: str) -> tuple[Hit, ...]:
    hits = []
    for line in (text or "").splitlines():
        match = _GREP.match(line)
        if match:
            hits.append(Hit(PASTED, match.group("path").removeprefix("./"), (match.group("text").strip(),), "",
                            int(match.group("line"))))
    return tuple(hits)


def plan_env(request_env: str, workspace: str) -> str:
    if request_env:
        return request_env
    from_workspace = environment(workspace) if workspace else "unknown"
    return from_workspace if from_workspace != "unknown" else (_env_word(workspace) or "unknown")
