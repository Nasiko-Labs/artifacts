"""Pure: mapping a discovered upload tool's inputSchema to arguments by property-name patterns, choosing a
folder-lookup tool, and reading folders and links out of tool output. If a required property cannot be
mapped (or only a path/URL input exists), there is no upload: the fallback is offered instead."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import McpTool
from send_guard import is_write_tool, name_tokens

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MAX_FOLDERS = 10
_NAME = re.compile(r"^(?:name|file_?name|title)$", re.IGNORECASE)
_CONTENT = re.compile(r"^(?:content|file_?content|data|base64|file|.*base64.*)$", re.IGNORECASE)
_MIME = re.compile(r"^(?:mime_?type|content_?type)$", re.IGNORECASE)
_FOLDER = re.compile(r"^(?:folder_?id|parent|parents|folder|parent_?id)$", re.IGNORECASE)
_QUERY = re.compile(r"^(?:query|q|name|folder_?name|search|search_?query)$", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


@dataclass(frozen=True)
class UploadPlan:
    tool: str
    name_key: str
    content_key: str
    mime_key: str | None = None
    folder_key: str | None = None
    folder_is_list: bool = False

    def arguments(self, file_name: str, data_b64: str, folder_id: str | None) -> dict:
        args: dict[str, Any] = {self.name_key: file_name, self.content_key: data_b64}
        if self.mime_key:
            args[self.mime_key] = PPTX_MIME
        if self.folder_key and folder_id:
            args[self.folder_key] = [folder_id] if self.folder_is_list else folder_id
        return args

    def to_dict(self) -> dict:
        return {"tool": self.tool, "name_key": self.name_key, "content_key": self.content_key,
                "mime_key": self.mime_key, "folder_key": self.folder_key, "folder_is_list": self.folder_is_list}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> UploadPlan:
        return UploadPlan(tool=str(data["tool"]), name_key=str(data["name_key"]),
                          content_key=str(data["content_key"]), mime_key=data.get("mime_key") or None,
                          folder_key=data.get("folder_key") or None, folder_is_list=bool(data.get("folder_is_list")))


def _props(tool: McpTool) -> dict[str, Mapping[str, Any]]:
    props = tool.input_schema.get("properties")
    return {str(k): v for k, v in props.items() if isinstance(v, Mapping)} if isinstance(props, Mapping) else {}


def _first(props: dict[str, Mapping[str, Any]], pattern: re.Pattern) -> str | None:
    return next((k for k in props if pattern.match(k)), None)


def map_upload(tool: McpTool) -> UploadPlan | None:
    props = _props(tool)
    name_key, content_key = _first(props, _NAME), _first(props, _CONTENT)
    if name_key is None or content_key is None or props[content_key].get("type") not in ("string", None):
        return None
    content_text = f"{content_key} {props[content_key].get('description', '')}".casefold()
    if "base64" not in content_text:
        return None  # raw bytes cannot travel as plain text
    folder_key = _first(props, _FOLDER)
    plan = UploadPlan(tool.name, name_key, content_key, _first(props, _MIME), folder_key,
                      bool(folder_key and props[folder_key].get("type") == "array"))
    mapped = {plan.name_key, plan.content_key, plan.mime_key, plan.folder_key} - {None}
    required = tool.input_schema.get("required")
    missing = [r for r in required if r not in mapped] if isinstance(required, list) else []
    return None if missing else plan


def is_upload_candidate(tool: McpTool) -> bool:
    tokens = set(name_tokens(tool.name))
    return is_write_tool(tool.name) and ("upload" in tokens or {"create", "file"} <= tokens)


def pick_upload(tools: Iterable[McpTool]) -> tuple[UploadPlan | None, bool]:
    """(the first mappable upload tool's plan, whether any upload-like tool was seen at all)."""
    candidates = [t for t in tools if is_upload_candidate(t)]
    plan = next((p for p in (map_upload(t) for t in candidates) if p is not None), None)
    return plan, bool(candidates)


def pick_folder_lookup(tools: Iterable[McpTool]) -> tuple[McpTool, str] | None:
    for tool in tools:
        tokens = set(name_tokens(tool.name))
        if is_write_tool(tool.name) or "folder" not in tokens and "folders" not in tokens:
            continue
        if tokens & {"find", "search", "list", "get"}:
            key = _first(_props(tool), _QUERY)
            if key:
                return tool, key
    return None


def parse_folders(text: str) -> list[tuple[str, str]]:
    """(id, name) pairs found in tool output, folders first when a mime type says so."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    found: list[tuple[str, str, bool]] = []
    _walk(data, found)
    folders = [f for f in found if f[2]] or found
    return list(dict.fromkeys((i, n) for i, n, _ in folders))[:MAX_FOLDERS]


def _walk(value: Any, found: list) -> None:
    if isinstance(value, Mapping):
        ident, name = value.get("id"), value.get("name") or value.get("title")
        if isinstance(ident, str) and isinstance(name, str):
            found.append((ident, name, "folder" in str(value.get("mimeType") or value.get("mime_type") or "")))
        for v in value.values():
            _walk(v, found)
    elif isinstance(value, list):
        for v in value:
            _walk(v, found)


def links(text: str) -> list[str]:
    return list(dict.fromkeys(_URL.findall(text)))
