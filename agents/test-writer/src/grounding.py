"""Pure: verbatim checks of model text against the pasted source (normalised: NFKC, smart quotes -> straight,
whitespace collapsed)."""

from __future__ import annotations

import unicodedata

from census import Census


def normalise(text: str) -> str:
    plain = unicodedata.normalize("NFKC", text or "")
    plain = plain.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return " ".join(plain.split())


def verified_bug(raw, census: Census) -> dict | None:
    """The model's suspected bug, kept only when its quote is in the source verbatim (normalised)."""
    if not isinstance(raw, dict):
        return None
    quote = normalise(str(raw.get("quote") or ""))
    target = next((c for c in census.selected if str(raw.get("callable") or "") in (c.name, c.short)), None)
    if len(quote) < 4 or target is None or quote not in normalise(target.source):
        return None
    return {"callable": target.name, "quote": quote, "why": " ".join(str(raw.get("why") or "").split())[:300]}
