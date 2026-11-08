"""Fixed colour-blind-safe palette (Okabe-Ito order), readable on light and dark backgrounds."""

SERIES_COLOURS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00")


def colour(index: int) -> str:
    return SERIES_COLOURS[index % len(SERIES_COLOURS)]
