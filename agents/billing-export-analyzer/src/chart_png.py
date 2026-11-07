"""The optional PNG (API clients only: the chat drops agent file parts). matplotlib is imported lazily, only
when a PNG is asked for; any failure returns None and the reply says "PNG unavailable"."""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping
from typing import Any

log = logging.getLogger("billing-export-analyzer.png")

FILENAME = "spend-by-service.png"
WIDTH_IN, HEIGHT_IN, DPI = 9, 4.5, 100
# Okabe-Ito, colour-blind safe.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#999999"]


def render(data: Mapping[str, Any], title: str) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except Exception:  # noqa: BLE001 - any import problem means "no PNG", never a failed turn
        log.warning("matplotlib unavailable")
        return None
    try:
        series = {k: v for k, v in data["series"].items() if k != "Total"}
        fig, ax = plt.subplots(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI)
        ax.stackplot(range(len(data["x"])), *series.values(), labels=list(series), colors=PALETTE[:len(series)])
        ax.set_xticks(range(len(data["x"])), data["x"], rotation=45, fontsize=8)
        ax.set_title(title)
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        return buffer.getvalue()
    except Exception:
        log.exception("PNG render failed")
        return None
