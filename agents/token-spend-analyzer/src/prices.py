"""Pure: the built-in price table (USD per 1M tokens) used only when an export has tokens but no cost, plus the
user's own `price_table` override ("gpt-4o-mini input $0.15 output $0.60 per 1M").

Seed values are list prices as known to the builder; they could not be re-checked against the providers on the
build date, so the reply always prints AS_OF and labels any cost computed from them as `computed`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

AS_OF = "2026-11-06 (seed list prices, unverified)"
PREMIUM, STANDARD, SMALL = "premium", "standard", "small"
ANTHROPIC_CACHE_WRITE_MULTIPLIER = 1.25
PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class Price:
    provider: str
    tier: str
    input: float
    cached_input: float
    output: float
    smaller: str | None = None   # the family's small sibling a "model overkill" estimate is priced at


SEED: dict[str, Price] = {
    "gpt-4o-mini": Price("openai", SMALL, 0.15, 0.075, 0.60),
    "gpt-4o": Price("openai", PREMIUM, 2.50, 1.25, 10.00, "gpt-4o-mini"),
    "gpt-4.1-nano": Price("openai", SMALL, 0.10, 0.025, 0.40),
    "gpt-4.1-mini": Price("openai", SMALL, 0.40, 0.10, 1.60),
    "gpt-4.1": Price("openai", PREMIUM, 2.00, 0.50, 8.00, "gpt-4.1-mini"),
    "o3-mini": Price("openai", STANDARD, 1.10, 0.55, 4.40),
    "o4-mini": Price("openai", STANDARD, 1.10, 0.275, 4.40),
    "claude-3-5-haiku": Price("anthropic", SMALL, 0.80, 0.08, 4.00),
    "claude-haiku": Price("anthropic", SMALL, 1.00, 0.10, 5.00),
    "claude-3-5-sonnet": Price("anthropic", PREMIUM, 3.00, 0.30, 15.00, "claude-3-5-haiku"),
    "claude-3-7-sonnet": Price("anthropic", PREMIUM, 3.00, 0.30, 15.00, "claude-3-5-haiku"),
    "claude-sonnet": Price("anthropic", PREMIUM, 3.00, 0.30, 15.00, "claude-haiku"),
    "claude-opus": Price("anthropic", PREMIUM, 15.00, 1.50, 75.00, "claude-haiku"),
    "claude-3-opus": Price("anthropic", PREMIUM, 15.00, 1.50, 75.00, "claude-3-5-haiku"),
    "gemini-2.0-flash": Price("google", SMALL, 0.10, 0.025, 0.40),
    "gemini-2.5-flash": Price("google", SMALL, 0.30, 0.075, 2.50),
    "gemini-1.5-pro": Price("google", PREMIUM, 1.25, 0.3125, 5.00, "gemini-2.0-flash"),
    "gemini-2.5-pro": Price("google", PREMIUM, 1.25, 0.31, 10.00, "gemini-2.5-flash"),
}

_OVERRIDE = re.compile(
    r"([a-z0-9][\w.\-/:]*)\s*:?\s+input\s*\$?\s*([\d.]+)(?:\s*,?\s*cached(?:\s+input)?\s*\$?\s*([\d.]+))?"
    r"\s*,?\s*output\s*\$?\s*([\d.]+)", re.IGNORECASE)


def norm_model(name: str) -> str:
    return str(name or "").strip().casefold().rsplit("/", 1)[-1]


def lookup(model: str, table: Mapping[str, Price] | None = None) -> Price | None:
    """The longest key the model name starts with ("gpt-4o-2024-08-06" → gpt-4o, not gpt-4o-mini)."""
    name = norm_model(model)
    if not name:
        return None
    prices = table if table is not None else SEED
    keys = [k for k in prices if name == k or name.startswith((k + "-", k + "@"))]
    return prices[max(keys, key=len)] if keys else None


def parse_overrides(instruction: str) -> dict[str, Price]:
    """"<model> input $x [cached $y] output $z per 1M": a known model keeps its provider, tier and sibling."""
    out: dict[str, Price] = {}
    for m in _OVERRIDE.finditer(instruction or ""):
        name = norm_model(m.group(1))
        try:
            inp, out_price = float(m.group(2)), float(m.group(4))
            cached = float(m.group(3)) if m.group(3) else inp
        except ValueError:
            continue
        base = lookup(name)
        out[name] = Price(base.provider if base else "custom", base.tier if base else STANDARD, inp, cached,
                          out_price, base.smaller if base else None)
    return out


def table_with(overrides: Mapping[str, Price] | None) -> dict[str, Price]:
    return {**SEED, **(overrides or {})}


def token_cost(price: Price, input_tokens: float, output_tokens: float, cached: float = 0.0,
               cache_write: float = 0.0) -> float:
    """`input_tokens` includes cached reads and cache writes (the frame's convention)."""
    plain = np.maximum(input_tokens - cached - cache_write, 0.0)
    total = (plain * price.input + cached * price.cached_input
             + cache_write * price.input * ANTHROPIC_CACHE_WRITE_MULTIPLIER + output_tokens * price.output)
    return total / PER_MILLION


def input_cost(price: Price, input_tokens: float, cached: float = 0.0) -> float:
    return (np.maximum(input_tokens - cached, 0.0) * price.input + cached * price.cached_input) / PER_MILLION


def output_cost(price: Price, output_tokens: float) -> float:
    return output_tokens * price.output / PER_MILLION
