"""The optional PNG chart (matplotlib, Agg backend, imported lazily): 900×450, titled, axis labels, a
colour-blind-safe colour. Only built when the user asks for a PNG; any failure returns None and the reply
says "PNG unavailable"."""

from __future__ import annotations

import io
import logging

log = logging.getLogger("csv_to_insight.png")

WIDTH_IN, HEIGHT_IN, DPI = 9, 4.5, 100
COLOUR = "#0072B2"  # Okabe-Ito blue
MAX_TICKS = 12


def render_png(chart: dict | None) -> bytes | None:
    if not chart:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except Exception as e:  # noqa: BLE001 - missing library: the text chart still stands
        log.warning("matplotlib unavailable: %s", type(e).__name__)
        return None
    try:
        return _draw(plt, chart)
    except Exception as e:  # noqa: BLE001
        log.warning("PNG chart failed: %s", type(e).__name__)
        return None


def _draw(plt, chart: dict) -> bytes:
    name, values = next(iter(chart["series"].items()))
    labels = [str(x) for x in chart["x"]]
    fig, ax = plt.subplots(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI)
    try:
        positions = list(range(len(labels)))
        if chart["kind"] == "line":
            ax.plot(positions, [float("nan") if v is None else v for v in values], color=COLOUR, marker="o")
        else:
            ax.bar(positions, values, color=COLOUR)
        step = max(1, len(labels) // MAX_TICKS)
        ax.set_xticks(positions[::step], labels[::step], rotation=30, ha="right", fontsize=8)
        ax.set_title(str(chart.get("title") or name))
        ax.set_ylabel(name)
        ax.set_xlabel(str(chart.get("x_label") or ""))
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        return buffer.getvalue()
    finally:
        plt.close(fig)
