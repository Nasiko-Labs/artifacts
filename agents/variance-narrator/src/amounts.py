"""Pure: one spreadsheet cell to a Decimal. Handles `$1,234`, `1.2M`, `850k`, `(500)`, `-` (zero), `n/a`
(skipped) and European `1.234,56` (the last separator is the decimal one when both appear)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

SYMBOLS = "$€£¥₹"
CODES = {"usd": "$", "eur": "€", "gbp": "£", "jpy": "¥", "inr": "₹"}
OK, BLANK, NA, BAD = "ok", "blank", "na", "bad"
_SCALE = {"k": Decimal(1_000), "m": Decimal(1_000_000), "mm": Decimal(1_000_000), "b": Decimal(1_000_000_000),
          "bn": Decimal(1_000_000_000)}
_BLANKS = {"", "-", "--", "—", "–"}
_NAS = {"n/a", "na", "#n/a", "nm", "n.a.", "none", "tbd", "?"}
_CODE = re.compile(r"\b(usd|eur|gbp|jpy|inr)\b", re.IGNORECASE)
_BODY = re.compile(r"^([0-9][0-9.,' ]*)(k|m|mm|b|bn)?$", re.IGNORECASE)
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3}){2,}$")


@dataclass(frozen=True)
class Cell:
    value: Decimal | None
    status: str
    symbol: str = ""


def parse_amount(raw: str) -> Cell:
    text = (raw or "").strip().strip("*").strip()
    if text.casefold() in _BLANKS:
        return Cell(Decimal(0), BLANK)
    if text.casefold() in _NAS:
        return Cell(None, NA)
    symbol = next((s for s in SYMBOLS if s in text), "")
    code = _CODE.search(text)
    if not symbol and code:
        symbol = CODES[code.group(1).casefold()]
    body = _CODE.sub("", text)
    for s in SYMBOLS:
        body = body.replace(s, "")
    body = body.replace(" ", " ").strip()
    if "%" in body:
        return Cell(None, BAD, symbol)
    negative = False
    if body.startswith("(") and body.endswith(")"):
        negative, body = True, body[1:-1].strip()
    if body.startswith(("-", "−")):
        negative, body = not negative, body[1:].strip()
    if body.endswith("-"):
        negative, body = not negative, body[:-1].strip()
    match = _BODY.match(body)
    if not match:
        return Cell(None, BAD, symbol)
    number = _number(match.group(1).replace(" ", "").replace("'", ""))
    if number is None:
        return Cell(None, BAD, symbol)
    if match.group(2):
        number *= _SCALE[match.group(2).casefold()]
    return Cell(-number if negative else number, OK, symbol)


def _number(text: str) -> Decimal | None:
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        text = text.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in text:
        text = text.replace(",", "") if _THOUSANDS_COMMA.match(text) else text.replace(",", ".")
    elif text.count(".") > 1:
        if not _THOUSANDS_DOT.match(text):
            return None
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def symbol_in(text: str) -> str:
    """The first currency symbol (or ISO code) named in free text, else ''."""
    found = next((s for s in SYMBOLS if s in (text or "")), "")
    if found:
        return found
    code = _CODE.search(text or "")
    return CODES[code.group(1).casefold()] if code else ""
