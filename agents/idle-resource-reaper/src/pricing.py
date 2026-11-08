"""Pure: list-price estimates of what an idle resource costs a month. Every number here is a constant;
the model never sees a price it could change, and never computes one.

Rates are us-east-1 on-demand list prices (USD), Linux for instances, the public price list as known at
build time. PRICES_AS_OF records that date; estimates are always labelled as estimates, and a numeric
`monthly_cost` the user pasted always wins over them.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

PRICES_AS_OF = "2026-11-07"
CURRENCY = "USD"
HOURS_PER_MONTH = Decimal(730)
ESTIMATED_BASIS = "estimated, us-east-1 on-demand list price"
REPORTED_BASIS = "reported"
CENT = Decimal("0.01")

# Block storage, per GB-month.
VOLUME_RATES: dict[str, Decimal] = {
    "gp3": Decimal("0.08"),
    "gp2": Decimal("0.10"),
    "io1": Decimal("0.125"),
    "io2": Decimal("0.125"),
    "st1": Decimal("0.045"),
    "sc1": Decimal("0.015"),
}
DEFAULT_VOLUME_TYPE = "gp3"  # what a volume with no type is priced as (labelled in the detail)
SNAPSHOT_RATE = Decimal("0.05")          # per GB-month
IP_HOURLY = Decimal("0.005")             # public IPv4 address, per hour
LOAD_BALANCER_HOURLY = Decimal("0.0225")  # classic / application, hourly part only (LCU excluded)
NAT_GATEWAY_HOURLY = Decimal("0.045")    # hourly part only (data processing excluded)

# Instances, per hour. A type not listed here has an unknown cost and stays out of totals.
INSTANCE_HOURLY: dict[str, Decimal] = {
    "t3.micro": Decimal("0.0104"),
    "t3.small": Decimal("0.0208"),
    "t3.medium": Decimal("0.0416"),
    "t3.large": Decimal("0.0832"),
    "m5.large": Decimal("0.096"),
    "m5.xlarge": Decimal("0.192"),
    "c5.large": Decimal("0.085"),
    "r5.large": Decimal("0.126"),
}


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def monthly(hourly: Decimal) -> Decimal:
    return cents(hourly * HOURS_PER_MONTH)


def volume_rate(volume_type: str) -> Decimal | None:
    return VOLUME_RATES.get((volume_type or DEFAULT_VOLUME_TYPE).strip().casefold())


def volume_monthly(size_gb: float | None, volume_type: str) -> Decimal | None:
    rate = volume_rate(volume_type)
    if size_gb is None or rate is None:
        return None
    return cents(Decimal(str(size_gb)) * rate)


def snapshot_monthly(size_gb: float | None) -> Decimal | None:
    return None if size_gb is None else cents(Decimal(str(size_gb)) * SNAPSHOT_RATE)


def instance_monthly(instance_type: str) -> Decimal | None:
    hourly = INSTANCE_HOURLY.get((instance_type or "").strip().casefold())
    return None if hourly is None else monthly(hourly)


IP_MONTHLY = monthly(IP_HOURLY)                    # 3.65
LOAD_BALANCER_MONTHLY = monthly(LOAD_BALANCER_HOURLY)  # 16.43
NAT_GATEWAY_MONTHLY = monthly(NAT_GATEWAY_HOURLY)  # 32.85


def money(value: Decimal) -> str:
    """1240 → "1,240.00"."""
    return f"{cents(value):,.2f}"
