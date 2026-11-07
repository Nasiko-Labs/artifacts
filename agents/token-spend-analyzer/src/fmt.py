"""Pure: the one way numbers are written. The digest, the renderer and the checks all use these strings, so a
figure the model copies can be matched exactly."""

from __future__ import annotations

WHOLE_FROM = 100.0
CENTS_FROM = 0.01
THOUSAND, MILLION = 1_000.0, 1_000_000.0


def money(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "unknown"
    v = float(value)
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    a = abs(v)
    if a >= WHOLE_FROM:
        body = f"{a:,.0f}"
    elif a >= CENTS_FROM or a == 0:
        body = f"{a:,.2f}"
    else:
        body = f"{a:.4f}"
    return f"{sign}${body}"


def pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def count(value: float | None) -> str:
    """Tokens or requests: 41.2M, 312k, 950."""
    if value is None:
        return "n/a"
    v = float(value)
    if abs(v) >= MILLION:
        return f"{v / MILLION:.1f}M"
    if abs(v) >= THOUSAND * 10:
        return f"{v / THOUSAND:.0f}k"
    return f"{v:,.0f}"


def ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def safe(text: object) -> str:
    """Untrusted names in markdown: no raw HTML, no table breaks, no image syntax, one line."""
    out = str(text if text is not None else "").replace("![", "[")
    for ch in "<>|\r\n":
        out = out.replace(ch, " ")
    return " ".join(out.split())
