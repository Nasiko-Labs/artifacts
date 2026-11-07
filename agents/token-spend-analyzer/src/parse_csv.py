"""Pure: a pasted CSV/TSV (OpenAI usage/cost, Anthropic usage, or a generic table) read as strings by pandas, at
most MAX_ROWS rows. Roles are mapped later (columns.py); numbers are parsed in frame.py."""

from __future__ import annotations

import csv
import io

import pandas as pd

MAX_ROWS = 200_000
SHAPE_OPENAI_HEADERS = frozenset({"n_context_tokens_total", "n_generated_tokens_total", "num_model_requests",
                                  "snapshot_id", "amount_value", "input_cached_tokens"})
SHAPE_ANTHROPIC_HEADERS = frozenset({"cache_read_input_tokens", "cache_creation_input_tokens"})
SHAPE_GATEWAY_HEADERS = frozenset({"spend", "request_tags", "starttime", "cache_hit", "team_id", "api_base"})


def delimiter_of(line: str) -> str:
    counts = {d: line.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def fields(line: str, delimiter: str) -> int:
    return len(next(csv.reader([line], delimiter=delimiter), []))


def read(text: str) -> tuple[pd.DataFrame, bool]:
    """(frame of strings, capped). Raises ValueError when the text is not a table."""
    first = next((x for x in text.splitlines() if x.strip()), "")
    try:
        df = pd.read_csv(io.StringIO(text), sep=delimiter_of(first), dtype=str, keep_default_na=False,
                         nrows=MAX_ROWS + 1, skipinitialspace=True, on_bad_lines="skip", engine="python")
    except (pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error) as e:
        raise ValueError(f"not a readable table: {type(e).__name__}") from e
    df.columns = [str(c).strip() for c in df.columns]
    capped = len(df) > MAX_ROWS
    return (df.head(MAX_ROWS) if capped else df), capped


def shape_of(normalised_headers: set[str]) -> str:
    if normalised_headers & SHAPE_ANTHROPIC_HEADERS:
        return "anthropic"
    if normalised_headers & SHAPE_OPENAI_HEADERS:
        return "openai"
    if normalised_headers & SHAPE_GATEWAY_HEADERS:
        return "gateway"
    return "generic"
