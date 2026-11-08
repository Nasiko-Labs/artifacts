"""Pure: the files a storage tool's search or metadata result lists, read from its JSON in code.

Only values present in the tool output are used; folders are skipped; entries are deduplicated by id."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_NAME_KEYS = ("name", "title", "filename", "file_name")
_ID_KEYS = ("id", "file_id", "fileid", "path_lower", "path_display", "path")
_SIZE_KEYS = ("size", "bytes", "filesize", "file_size")
_MIME_KEYS = ("mimetype", "mime_type", "mime")
_MODIFIED_KEYS = ("modifiedtime", "server_modified", "client_modified", "modified", "modified_at", "updated")
_FOLDER_MIMES = ("application/vnd.google-apps.folder",)
LABEL_MAX_BYTES = 200


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    folder: str | None = None
    modified: str | None = None
    size: int | None = None
    mime: str | None = None
    place: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d: dict) -> Entry:
        return Entry(**{k: d.get(k) for k in ("id", "name", "folder", "modified", "size", "mime", "place")})

    def label(self) -> str:
        facts = [f for f in (self.folder, (self.modified or "")[:10] or None) if f]
        text = f"{self.name} ({', '.join(facts)})" if facts else self.name
        return _clip(text, LABEL_MAX_BYTES)


def entries(raw_text: str) -> list[Entry]:
    try:
        body = json.loads(raw_text)
    except ValueError:
        return []
    found: dict[str, Entry] = {}
    for d in _dicts(body):
        entry = _entry(d)
        if entry is not None:
            found.setdefault(entry.id, entry)
    return list(found.values())


def merge(base: Entry, meta: Entry | None) -> Entry:
    if meta is None:
        return base
    return Entry(base.id, base.name or meta.name, base.folder or meta.folder, base.modified or meta.modified,
                 base.size if base.size is not None else meta.size, base.mime or meta.mime, base.place)


def matches(found: list[Entry], name: str) -> list[Entry]:
    wanted = name.casefold().strip()
    exact = [e for e in found if e.name.casefold() == wanted]
    if exact:
        return exact
    stem = wanted.rsplit(".", 1)[0] if "." in wanted else wanted
    return [e for e in found if stem and stem in e.name.casefold()]


def _entry(d: dict) -> Entry | None:
    name = _first(d, _NAME_KEYS)
    ident = _first(d, _ID_KEYS)
    if not isinstance(name, str) or not name.strip() or not isinstance(ident, str) or not ident.strip():
        return None
    mime = _first(d, _MIME_KEYS)
    if mime in _FOLDER_MIMES or str(d.get(".tag", "")).casefold() == "folder" or d.get("is_dir") is True:
        return None
    return Entry(id=ident, name=name.strip(), folder=_folder(d), modified=_str(_first(d, _MODIFIED_KEYS)),
                 size=_size(_first(d, _SIZE_KEYS)), mime=_str(mime))


def _folder(d: dict) -> str | None:
    path = _first(d, ("path_display",))
    if isinstance(path, str) and "/" in path.strip("/"):
        return path.rsplit("/", 1)[0] or "/"
    for key in ("folder", "parent_name", "directory"):
        if isinstance(d.get(key), str) and d[key].strip():
            return d[key].strip()
    return None


def _size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    lowered = {k.casefold(): v for k, v in d.items() if isinstance(k, str)}
    return next((lowered[k] for k in keys if lowered.get(k) not in (None, "")), None)


def _str(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int)) and str(value) else None


def _dicts(node: Any) -> Iterator[dict]:
    queue = [node]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            yield current
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)


def _clip(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit - 3].decode("utf-8", errors="ignore") + "..."
