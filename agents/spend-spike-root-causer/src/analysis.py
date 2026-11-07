"""Pure: the whole code-side analysis of one export: parse, pick the currency, detect, trace the chain.

Takes milliseconds and is re-run from the stored export text whenever a later turn needs it (a new window, the
draft, the post), so only the export and the user's hints are kept between turns."""

from __future__ import annotations

from dataclasses import dataclass

import aggregate as agg
import chain as chain_
import detect
from export import Export, ParseError, Row, parse

NO_ROWS = ("I found the export's header but could not read any row (every date or cost was unreadable). Check the "
           "`date` (YYYY-MM-DD) and `cost` columns and paste it again.")


@dataclass(frozen=True)
class Analysis:
    export: Export
    currency: str
    rows: tuple[Row, ...]               # rows in `currency` only; amounts are never converted
    other_currencies: tuple[str, ...]
    window_days: int
    result: detect.Spike | detect.ShortHistory
    chain: chain_.Chain | None          # only when a spike was found
    movers: tuple[detect.Mover, ...]    # no spike, or short history

    @property
    def spike(self) -> detect.Spike | None:
        r = self.result
        return r if isinstance(r, detect.Spike) and r.is_spike else None

    @property
    def bad_share_high(self) -> bool:
        return self.export.data_rows > 0 and self.export.bad_rows * 10 > self.export.data_rows


def analyse(table: str, window_days: int | None, budget_micros: int | None) -> Analysis | ParseError:
    parsed = parse(table)
    if isinstance(parsed, ParseError):
        return parsed
    if not parsed.rows:
        return ParseError(NO_ROWS)
    currency = agg.main_currency(parsed.rows)
    rows = tuple(r for r in parsed.rows if r.currency == currency)
    others = tuple(sorted({r.currency for r in parsed.rows} - {currency}))
    window = agg.clamp_window(window_days)
    result = detect.detect(rows, window, budget_micros)
    if isinstance(result, detect.ShortHistory):
        return Analysis(parsed, currency, rows, others, window, result, None, result.movers)
    if not result.is_spike:
        return Analysis(parsed, currency, rows, others, window, result, None,
                        detect.top_movers(rows, result.periods))
    traced = chain_.trace(rows, result.periods, parsed.tag_keys, parsed.dimensions)
    return Analysis(parsed, currency, rows, others, window, result, traced, ())
