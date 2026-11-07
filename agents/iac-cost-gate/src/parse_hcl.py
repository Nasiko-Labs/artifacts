"""Pure: Terraform HCL to plain blocks (python-hcl2). Quoted strings are unquoted; expressions stay as
"${...}" strings for expand.py. A file that does not parse whole is parsed block by block, and every
block that still fails is named in a note (never skipped silently)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import hcl2
from lark.exceptions import LarkError

log = logging.getLogger("iac-cost-gate.hcl")
PARSE_ERRORS = (LarkError, ValueError, TypeError, KeyError, AttributeError, IndexError)

_META_KEYS = frozenset({"__is_block__", "__start_line__", "__end_line__"})
_BLOCK_START = re.compile(r'^([A-Za-z_][\w-]*)((?:\s+(?:"[^"]*"|[\w-]+))*)\s*\{')
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


@dataclass
class HclDoc:
    resources: list[tuple[str, str, dict]] = field(default_factory=list)   # (type, name, body)
    variables: dict[str, Any] = field(default_factory=dict)
    locals: dict[str, Any] = field(default_factory=dict)
    providers: list[tuple[str, dict]] = field(default_factory=list)
    modules: list[tuple[str, str]] = field(default_factory=list)          # (name, source)
    notes: list[str] = field(default_factory=list)
    blocks_failed: int = 0


def unquote(key: Any) -> str:
    k = str(key)
    return k[1:-1] if len(k) >= 2 and k.startswith('"') and k.endswith('"') else k


def norm(value: Any) -> Any:
    if isinstance(value, dict):
        return {unquote(k): norm(v) for k, v in value.items() if k not in _META_KEYS}
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, str) and len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"')
    return value


def parse(text: str) -> HclDoc:
    doc = HclDoc()
    try:
        _collect(doc, hcl2.loads(_ensure_newline(text)))
        return doc
    except PARSE_ERRORS as e:  # the block-by-block fallback below names what failed
        log.info("whole-file HCL parse failed (%s); parsing block by block", type(e).__name__)
    for header, block in split_blocks(text):
        try:
            _collect(doc, hcl2.loads(_ensure_newline(block)))
        except PARSE_ERRORS:
            doc.blocks_failed += 1
            doc.notes.append(f"Could not parse the block `{header}`; it is not included.")
    return doc


def split_blocks(text: str) -> list[tuple[str, str]]:
    """Top-level blocks by brace depth (strings ignored). Text outside blocks (comments) is dropped."""
    blocks, current, depth, header = [], [], 0, ""
    for line in text.replace("\r\n", "\n").split("\n"):
        if depth == 0:
            match = _BLOCK_START.match(line.strip())
            if not match:
                continue
            header = " ".join((match.group(1) + match.group(2)).split())
        current.append(line)
        bare = _STRING.sub("", line.split("#", 1)[0] if '"' not in line else line)
        depth += bare.count("{") - bare.count("}")
        if depth <= 0:
            blocks.append((header, "\n".join(current)))
            current, depth = [], 0
    if current:
        blocks.append((header, "\n".join(current)))
    return blocks


def _ensure_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _collect(doc: HclDoc, raw: dict) -> None:
    data = norm(raw)
    for entry in data.get("resource", []):
        for rtype, named in entry.items():
            for name, body in named.items():
                for one in body if isinstance(body, list) else [body]:
                    doc.resources.append((rtype, name, one if isinstance(one, dict) else {}))
    for entry in data.get("variable", []):
        for name, body in entry.items():
            if isinstance(body, dict) and "default" in body:
                doc.variables[name] = body["default"]
    for entry in data.get("locals", []):
        doc.locals.update(entry)
    for entry in data.get("provider", []):
        doc.providers += [(name, body) for name, body in entry.items() if isinstance(body, dict)]
    for entry in data.get("module", []):
        doc.modules += [(name, str((body or {}).get("source") or "")) for name, body in entry.items()]
