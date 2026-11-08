"""Pure: arguments for a storage tool, built from its input schema in code (no model involved).

A search tool gets the file name in its search property; when the schema says the property takes a query
expression ("name contains ..."), an exact-name expression first, then a contains one. A metadata or
download tool gets the file id in its id/path property; an export tool also gets a Markdown-friendly
format for a native document."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nasiko_mcp import McpTool

SEARCH_PROPS = ("query", "q", "search_query", "search", "query_term", "term", "keyword", "file_name", "filename",
                "name")
ID_PROPS = ("file_id", "fileid", "id", "path", "file_path", "path_display", "path_lower", "file")
MIME_PROPS = ("mime_type", "mimetype", "export_mime_type", "exportmimetype", "target_mime_type", "format")
_EXPRESSION_HINTS = ("name contains", "name =", "query syntax", "query string", "search query language",
                     "query expression")
_NATIVE = "application/vnd.google-apps."
EXPORTS = {
    "document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
}


def _props(tool: McpTool) -> Mapping[str, Any]:
    props = tool.input_schema.get("properties")
    return props if isinstance(props, Mapping) else {}


def _pick(tool: McpTool, names: tuple[str, ...]) -> str | None:
    by_lower = {k.casefold(): k for k in _props(tool)}
    return next((by_lower[n] for n in names if n in by_lower), None)


def _complete(tool: McpTool, args: dict) -> dict | None:
    """None when a required property is still missing (the tool cannot be called without a guess)."""
    required = tool.input_schema.get("required")
    missing = [r for r in required if r not in args] if isinstance(required, list) else []
    return None if missing else args


def takes_expression(tool: McpTool) -> bool:
    prop = _pick(tool, SEARCH_PROPS)
    spec = _props(tool).get(prop) if prop else None
    text = f"{spec.get('description', '') if isinstance(spec, Mapping) else ''} {tool.description}".casefold()
    return any(h in text for h in _EXPRESSION_HINTS)


def search_args(tool: McpTool, name: str, exact: bool) -> dict | None:
    prop = _pick(tool, SEARCH_PROPS)
    if prop is None:
        return None
    if takes_expression(tool):
        quoted = name.replace("\\", "\\\\").replace("'", "\\'")
        value = f"name = '{quoted}'" if exact else f"name contains '{quoted}'"
    else:
        value = name
    return _complete(tool, {prop: value})


def id_args(tool: McpTool, file_id: str, mime: str | None = None) -> dict | None:
    prop = _pick(tool, ID_PROPS) or _only_required_string(tool)
    if prop is None:
        return None
    args: dict[str, Any] = {prop: file_id}
    export = export_for(mime)
    mime_prop = _pick(tool, MIME_PROPS)
    if export and mime_prop:
        args[mime_prop] = _enum_choice(_props(tool).get(mime_prop), export[0])
    return _complete(tool, args)


def export_for(mime: str | None) -> tuple[str, str] | None:
    """(export media type, extension) for a native cloud document, else None."""
    if not mime or not mime.startswith(_NATIVE):
        return None
    return EXPORTS.get(mime[len(_NATIVE):])


def _enum_choice(spec: Any, wanted: str) -> str:
    options = spec.get("enum") if isinstance(spec, Mapping) else None
    if isinstance(options, list) and wanted not in options:
        return next((o for o in options if isinstance(o, str) and o.endswith(wanted.rsplit(".", 1)[-1])), wanted)
    return wanted


def _only_required_string(tool: McpTool) -> str | None:
    required = tool.input_schema.get("required")
    if not isinstance(required, list) or len(required) != 1:
        return None
    spec = _props(tool).get(required[0])
    return required[0] if isinstance(spec, Mapping) and spec.get("type") == "string" else None
