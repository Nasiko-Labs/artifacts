"""One file in, one Doc or Failure out: detect the type, run the fail-fast checks, convert in the worker,
clean the Markdown and compute facts and warnings. The model never sees any of it."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import postprocess
import sniff
from convert import Converter
from inputs import Incoming
from models import Doc, Failure

MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Blob:
    name: str
    source: str
    data: bytes
    mime: str = ""


def from_incoming(incoming: Incoming) -> list[Blob]:
    blobs = [Blob(f.name, "file", f.data, f.media_type) for f in incoming.files]
    for pasted in incoming.pasted:
        text = _tsv_to_csv(pasted.text) if pasted.kind == "tsv" else pasted.text
        ext = "html" if pasted.kind == "html" else "csv"
        blobs.append(Blob(f"pasted.{ext}", "pasted", text.encode("utf-8"), ""))
    return blobs


async def convert(converter: Converter, blob: Blob, options: dict) -> Doc | Failure:
    if len(blob.data) > MAX_BYTES:
        return Failure(name=blob.name, reason=f"is {len(blob.data) / (1024 * 1024):.1f} MB; the limit is 20 MB")
    sniffed = sniff.detect(blob.name, blob.mime, blob.data)
    if sniffed.failure or not sniffed.fmt:
        return Failure(name=blob.name, reason=sniffed.failure or "unsupported file type")
    fmt = sniffed.fmt
    refused = sniff.precheck(fmt, blob.data)
    if refused:
        return Failure(name=blob.name, reason=refused)
    result = await converter.convert(blob.data, fmt, _options(fmt, options))
    if not result.ok:
        return Failure(name=blob.name, reason=result.reason)
    cleaned = postprocess.clean(result.markdown, fmt, len(blob.data), result.units, result.sheets)
    if not cleaned.markdown.strip():
        reason = postprocess.SCANNED if cleaned.scanned else "no text could be extracted"
        return Failure(name=blob.name, reason=reason)
    warnings = ([sniffed.warning] if sniffed.warning else []) + list(cleaned.warnings)
    return Doc(name=blob.name, source=blob.source, format=fmt, bytes=len(blob.data), chars=len(cleaned.markdown),
               units=result.units, markdown=cleaned.markdown, warnings=warnings, scanned=cleaned.scanned)


def _options(fmt: str, options: dict) -> dict:
    """`pages` applies to PDFs only, `sheet` to workbooks only."""
    out = {}
    if fmt == "pdf" and options.get("pages"):
        out["pages"] = list(options["pages"])
    if fmt in ("xlsx", "xls") and options.get("sheet"):
        out["sheet"] = options["sheet"]
    return out


def _tsv_to_csv(text: str) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        writer.writerow(row)
    return buffer.getvalue()
