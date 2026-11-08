"""Pure: an inventory the user put in the message itself: a previous step's envelope, or a pasted table.
A two-line message that merely contains commas is not a table: the header must map at least one known
column, or the table must have two or more data rows."""

from __future__ import annotations

import headers
import table as tables
from table import Table

MIN_BODY_ROWS = 2


def read(text: str) -> Table | None:
    found = tables.from_envelope_text(text) or tables.from_text(text)
    if found is None or len(found.header) < 2:
        return None
    mapped = headers.map_headers(found.header)
    if mapped.fields or mapped.ambiguous or len(found.rows) >= MIN_BODY_ROWS:
        return found
    return None
