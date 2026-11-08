"""Pure: what a file really is, and the fail-fast checks that run before any converter sees it.

Type comes from the file name, the reported media type and the leading bytes. Binary magic wins over the
name (with a warning); for text the name decides, because sniffing text is weak. Zip containers are
checked from their central directory only (sizes and counts), so a zip bomb is refused unread."""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass

MAX_ZIP_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_MEMBERS = 20
PASSWORD = "password protected"

TEXT_FORMATS = ("csv", "tsv", "html", "json", "xml", "txt", "md")
ZIP_FORMATS = ("docx", "pptx", "xlsx", "epub", "zip")
SUPPORTED = ("pdf", "docx", "pptx", "xlsx", "xls", "epub", "zip", *TEXT_FORMATS)
_EXT = {"htm": "html", "markdown": "md", "text": "txt", **{f: f for f in SUPPORTED}}
_MIME = {
    "application/pdf": "pdf", "text/html": "html", "text/csv": "csv", "text/tab-separated-values": "tsv",
    "application/json": "json", "application/xml": "xml", "text/xml": "xml", "text/plain": "txt",
    "text/markdown": "md", "application/epub+zip": "epub", "application/zip": "zip",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")
_MEDIA_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"ID3", b"RIFF", b"OggS", b"\x1aE\xdf\xa3", b"fLaC")
_TAG = re.compile(r"<[a-zA-Z][^>]*>")


@dataclass(frozen=True)
class Sniffed:
    fmt: str | None
    warning: str | None = None
    failure: str | None = None


def from_name(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    return _EXT.get(ext)


def detect(name: str, media_type: str, data: bytes) -> Sniffed:
    named = from_name(name) or _MIME.get((media_type or "").split(";")[0].strip().casefold())
    magic, failure = _magic(data, named)
    if failure:
        return Sniffed(None, failure=failure)
    if magic is None:
        text_fmt = _text_format(data, named)
        if text_fmt is None:
            return Sniffed(None, failure="unsupported file type (images, audio and other binary files are not "
                                         "converted; no OCR in this agent)")
        return Sniffed(text_fmt)
    if named and named != magic and not (named == "zip" and magic in ZIP_FORMATS):
        return Sniffed(magic, warning=f"named .{named} but the content is {magic.upper()}; converted as {magic.upper()}")
    return Sniffed(magic)


def precheck(fmt: str, data: bytes) -> str | None:
    """A failure reason, or None when the file may go to the converter."""
    if fmt == "pdf" and b"/Encrypt" in data:
        return PASSWORD
    if fmt in ZIP_FORMATS:
        return _zip_check(fmt, data)
    return None


def _magic(data: bytes, named: str | None) -> tuple[str | None, str | None]:
    head = data[:8]
    if head.startswith(b"%PDF-"):
        return "pdf", None
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(data)
    if head.startswith(_OLE):
        if _ENCRYPTED_PACKAGE in data:
            return None, PASSWORD
        if named == "xls":
            return "xls", None
        return None, "legacy binary Office file (.doc/.ppt) is not supported; save it as .docx or .pptx"
    if head.startswith(_MEDIA_MAGIC):
        return None, "unsupported file type (images and audio are not converted; no OCR in this agent)"
    return None, None


def _zip_kind(data: bytes) -> tuple[str | None, str | None]:
    try:
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    except zipfile.BadZipFile:
        return None, "the file is damaged (not a readable zip container)"
    if "[Content_Types].xml" in names:
        for prefix, fmt in (("word/", "docx"), ("ppt/", "pptx"), ("xl/", "xlsx")):
            if any(n.startswith(prefix) for n in names):
                return fmt, None
    if "mimetype" in names and "META-INF/container.xml" in names:
        return "epub", None
    return "zip", None


def _zip_check(fmt: str, data: bytes) -> str | None:
    try:
        infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile:
        return "the file is damaged (not a readable zip container)"
    if len(infos) > MAX_ZIP_ENTRIES:
        return f"has {len(infos):,} entries inside; the limit is {MAX_ZIP_ENTRIES:,}"
    total = sum(i.file_size for i in infos)
    if total > MAX_UNCOMPRESSED_BYTES:
        return (f"unpacks to {total / (1024 * 1024):,.0f} MB; the limit is "
                f"{MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB (possible zip bomb)")
    members = [i for i in infos if not i.is_dir()]
    if fmt == "zip" and len(members) > MAX_ZIP_MEMBERS:
        return f"holds {len(members)} files; at most {MAX_ZIP_MEMBERS} are converted from one zip"
    return None


def _text_format(data: bytes, named: str | None) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    if named in TEXT_FORMATS:
        return named
    text = data[:4096].decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
    lowered = text[:200].casefold()
    if lowered.startswith("<?xml"):
        return "xml"
    if lowered.startswith(("<!doctype html", "<html")) or (text.startswith("<") and _TAG.search(text)):
        return "html"
    if text.startswith(("{", "[")) and _is_json(data):
        return "json"
    return "txt"


def _is_json(data: bytes) -> bool:
    try:
        json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return True
