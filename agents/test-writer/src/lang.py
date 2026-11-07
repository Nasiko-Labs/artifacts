"""Pure: split a pasted message into source files, the user's instruction and an optional existing test file,
and detect each file's language (header extension, then fence info string, then syntax signals)."""

from __future__ import annotations

import re
from dataclasses import dataclass

SUPPORTED = ("python", "javascript", "typescript", "go")
FAMILY = {"python": "python", "javascript": "js", "typescript": "js", "go": "go"}
EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rb": "ruby", ".java": "java", ".rs": "rust",
    ".cs": "c#", ".php": "php", ".kt": "kotlin", ".swift": "swift", ".c": "c", ".cpp": "c++", ".h": "c",
    ".scala": "scala", ".ex": "elixir",
}
FENCE_NAMES = {
    "python": "python", "py": "python", "python3": "python", "javascript": "javascript", "js": "javascript",
    "jsx": "javascript", "node": "javascript", "typescript": "typescript", "ts": "typescript",
    "tsx": "typescript", "go": "go", "golang": "go", "ruby": "ruby", "rb": "ruby", "java": "java", "rust": "rust",
    "rs": "rust", "csharp": "c#", "cs": "c#", "php": "php", "kotlin": "kotlin", "swift": "swift", "c": "c",
    "cpp": "c++", "c++": "c++", "scala": "scala", "elixir": "elixir",
}

_EXISTING = re.compile(r"^\s*existing tests?\s*:\s*$|^\s*existing tests?\s*:", re.IGNORECASE | re.MULTILINE)
_FENCE = re.compile(r"```[ \t]*([\w#+-]*)[^\n]*\n(.*?)(?:```|\Z)", re.DOTALL)
_HEADER = re.compile(r"^[ \t]*(?:#|//)[ \t]*file:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
_CODE_START = re.compile(
    r"^(?:(?:async )?def \w|class \w|import \w|from [\w.]+ import|@\w|function\b|export |const \w|let \w|var \w|"
    r"async function|package \w|func[ (]|type \w+ (?:struct|interface)|interface \w|module\.exports|"
    r"[\"']use strict[\"'])", re.MULTILINE)
_GENERATED = re.compile(r"Code generated .* DO NOT EDIT|@generated|auto-?generated", re.IGNORECASE)


@dataclass(frozen=True)
class SourceFile:
    path: str
    lang: str
    text: str
    generated: bool = False


@dataclass(frozen=True)
class Extracted:
    files: tuple[SourceFile, ...]
    instruction: str
    existing_tests: str = ""


def detect_syntax(text: str) -> str:
    """The language from code signals alone; "" when nothing looks like code."""
    if "<?php" in text:
        return "php"
    if re.search(r"^\s*public (?:static )?(?:final )?(?:class|void|int|String)\b", text, re.MULTILINE):
        return "java"
    if re.search(r"^\s*(?:pub )?fn \w+\(.*\)\s*(?:->|\{)", text, re.MULTILINE):
        return "rust"
    if re.search(r"^package \w+", text, re.MULTILINE) or re.search(r"^func (?:\([^)]*\) )?\w+\(", text, re.MULTILINE):
        return "go"
    defs = re.findall(r"^\s*(?:async )?def \w+.*$", text, re.MULTILINE)
    if defs:
        colon = any(d.rstrip().endswith(":") for d in defs) or re.search(r"\)\s*(?:->[^:\n]+)?:\s*$", text,
                                                                         re.MULTILINE)
        return "ruby" if not colon and re.search(r"^\s*end\s*$", text, re.MULTILINE) else "python"
    if re.search(r"^(?:from [\w.]+ import \w|import \w[\w.]*\s*$)", text, re.MULTILINE):
        return "python"
    js = re.search(r"\bfunction\b|=>|^\s*export |module\.exports|^\s*(?:const|let|var) \w+\s*=", text, re.MULTILINE)
    if js:
        ts = re.search(r":\s*(?:string|number|boolean|void|unknown|any)\b|^\s*interface \w+|^\s*type \w+ =", text,
                       re.MULTILINE)
        return "typescript" if ts else "javascript"
    return ""


def _lang_of(path: str, fence: str, text: str) -> str:
    ext = re.search(r"(\.\w+)$", path or "")
    if ext and ext.group(1).casefold() in EXTENSIONS:
        return EXTENSIONS[ext.group(1).casefold()]
    if fence and fence.casefold() in FENCE_NAMES:
        return FENCE_NAMES[fence.casefold()]
    return detect_syntax(text)


def _split_headers(chunk: str, fence: str) -> list[SourceFile]:
    headers = list(_HEADER.finditer(chunk))
    if not headers:
        return [_file("", fence, chunk)] if chunk.strip() else []
    files = [_file("", fence, chunk[:headers[0].start()])] if chunk[:headers[0].start()].strip() else []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(chunk)
        body = chunk[header.end():end].strip("\n")
        if body.strip():
            files.append(_file(header.group(1), fence, body))
    return files


def _file(path: str, fence: str, text: str) -> SourceFile:
    body = text.strip("\n")
    head = "\n".join(body.splitlines()[:5])
    return SourceFile(path, _lang_of(path, fence, body), body, bool(_GENERATED.search(head)))


def _strip_fences(text: str) -> str:
    blocks = [m.group(2) for m in _FENCE.finditer(text)]
    return "\n".join(blocks) if blocks else text


def extract(text: str) -> Extracted:
    """Source files, the instruction around them and an existing test file (after "existing tests:")."""
    raw = text or ""
    marker = _EXISTING.search(raw)
    existing = _strip_fences(raw[marker.end():]).strip() if marker else ""
    body = raw[:marker.start()] if marker else raw
    fences = list(_FENCE.finditer(body))
    if fences:
        files = [f for m in fences for f in _split_headers(m.group(2), m.group(1))]
        instruction = _FENCE.sub(" ", body)
    else:
        header = _HEADER.search(body)
        start = _CODE_START.search(body)
        cut = header.start() if header else (start.start() if start else None)
        if cut is None:
            return Extracted((), body.strip(), existing)
        files = _split_headers(body[cut:], "")
        instruction = body[:cut]
    files = [f for f in files if f.lang]
    return Extracted(tuple(files), " ".join(instruction.split()), existing)


def module_of(path: str, lang: str) -> str:
    """The import path for a file header: `shop/pricing.py` -> `shop.pricing`; JS keeps a relative path."""
    if not path:
        return ""
    stem = re.sub(r"\.\w+$", "", path.strip().lstrip("./"))
    if FAMILY.get(lang) == "python":
        stem = re.sub(r"^src/", "", stem)
        return ".".join(p for p in stem.split("/") if p and p != "__init__")
    return "./" + stem.split("/")[-1] if FAMILY.get(lang) == "js" else stem
