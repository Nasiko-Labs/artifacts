"""Subprocess entry: convert ONE file to Markdown and print a JSON result on stdout.

Run as `python -I converter_worker.py <file> <format> <options-json>` by convert.py. Isolated mode keeps
the agent's own modules off the path, so this file imports only the standard library and the converter
libraries. It caps its own address space and CPU time first, so a hostile file can only kill this
process. MarkItDown gets bytes (`convert_stream`), never a URL, and plugins stay off.
"""

from __future__ import annotations

import io
import json
import os
import resource
import sys

MEMORY_LIMIT_BYTES = int(os.environ.get("DOC_CONVERTER_WORKER_MEMORY_MB", "1536")) * 1024 * 1024
CPU_LIMIT_SECS = 60
MAX_PDF_PAGES = 200
_SLIDE_MARK = "<!-- Slide number:"


class Refused(Exception):
    """A reason to report as the file's failure (written for the user)."""


def _limit() -> None:
    for which, value in ((resource.RLIMIT_AS, MEMORY_LIMIT_BYTES), (resource.RLIMIT_CPU, CPU_LIMIT_SECS)):
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):  # not supported on this OS (macOS ignores RLIMIT_AS); the wall timeout stays
            pass


def _markitdown(data: bytes, fmt: str) -> str:
    from markitdown import MarkItDown, StreamInfo

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=f".{fmt}"))
    return result.markdown or ""


def _pdf(data: bytes, options: dict) -> dict:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        total = len(pdf.pages)
    pages = options.get("pages")
    if pages:
        first, last = int(pages[0]), int(pages[1])
        if not 1 <= first <= last <= total:
            raise Refused(f"the PDF has {total} pages; pages {first}-{last} is outside it")
        if last - first + 1 > MAX_PDF_PAGES:
            raise Refused(f"pages {first}-{last} is {last - first + 1} pages; at most {MAX_PDF_PAGES} at once")
        from pdfminer.high_level import extract_text

        text = extract_text(io.BytesIO(data), page_numbers=list(range(first - 1, last)))
        return {"markdown": text, "units": {"pages": last - first + 1}}
    if total > MAX_PDF_PAGES:
        raise Refused(f"has {total} pages; at most {MAX_PDF_PAGES} at once (ask for a range like 'pages 1-200')")
    return {"markdown": _markitdown(data, "pdf"), "units": {"pages": total}}


def _sheets(markdown: str) -> list[str]:
    return [line[3:].strip() for line in markdown.splitlines() if line.startswith("## ")]


def _workbook(data: bytes, fmt: str, options: dict) -> dict:
    markdown = _markitdown(data, fmt)
    names = _sheets(markdown)
    wanted = options.get("sheet")
    if wanted:
        match = next((n for n in names if n.casefold() == str(wanted).casefold()), None)
        if match is None:
            raise Refused(f"has no sheet named \"{wanted}\" (sheets: {', '.join(names) or 'none'})")
        markdown = _section(markdown, match)
        names = [match]
    return {"markdown": markdown, "units": {"sheets": len(names)}, "sheets": names}


def _section(markdown: str, name: str) -> str:
    lines = markdown.splitlines()
    start = lines.index(f"## {name}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def convert(data: bytes, fmt: str, options: dict) -> dict:
    if fmt == "pdf":
        return _pdf(data, options)
    if fmt in ("xlsx", "xls"):
        return _workbook(data, fmt, options)
    markdown = _markitdown(data, fmt)
    if fmt == "pptx":
        return {"markdown": markdown, "units": {"slides": markdown.count(_SLIDE_MARK)}}
    return {"markdown": markdown, "units": {}}


def _reason(error: BaseException) -> str:
    text = f"{type(error).__name__} {error}".casefold()
    if "password" in text or "encrypt" in text:
        return "password protected"
    if isinstance(error, MemoryError):
        return "too large to convert within this agent's memory limit"
    return "the converter could not read this file (it may be damaged)"


def main(argv: list[str]) -> int:
    _limit()
    path, fmt, options = argv[1], argv[2], json.loads(argv[3]) if len(argv) > 3 else {}
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        result = {"ok": True, **convert(data, fmt, options)}
    except Refused as e:
        result = {"ok": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001 - any converter failure fails this file only
        result = {"ok": False, "reason": _reason(e), "error": type(e).__name__}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
