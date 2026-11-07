"""Pure: the decline-reason table and the urgency of each failure, decided in code.

The class comes from the decline code first, then from keywords in the failure message. The model never
classifies; it only copies the code and message verbatim from tool output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from checks import parse_time

# class → (decline codes, recoverability, draft angle)
DECLINE_TABLE: dict[str, tuple[frozenset[str], str, str]] = {
    "card_expired": (frozenset({"expired_card", "invalid_expiry_year", "invalid_expiry_month"}), "high",
                     "ask the customer to update the card on file"),
    "insufficient_funds": (frozenset({"insufficient_funds"}), "medium",
                           "a friendly note; the automatic retry may succeed, or they can update the card"),
    "generic_decline": (frozenset({"generic_decline", "do_not_honor", "card_declined", "transaction_not_allowed"}),
                        "medium", "suggest contacting their bank or using another card"),
    "authentication_required": (frozenset({"authentication_required"}), "high",
                                "ask them to complete the card authentication or update the payment method"),
    "fraud_block": (frozenset({"fraudulent", "stolen_card", "lost_card", "pickup_card"}), "low",
                    "never mention fraud or a reason; ask for another payment method"),
    "incorrect_details": (frozenset({"incorrect_cvc", "incorrect_number", "incorrect_zip", "invalid_cvc",
                                     "invalid_number"}), "high", "ask them to re-enter the card details"),
    "unknown": (frozenset(), "low", "a neutral request to check the payment method"),
}
# processing_error is "incorrect_details" but only medium: it is usually transient.
_MEDIUM_DETAILS = frozenset({"processing_error"})
_THREE_D_SECURE = ("3d secure", "3ds", "authentication", "authenticate")
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("card_expired", ("expired", "expiry", "expiration")),
    ("insufficient_funds", ("insufficient funds", "insufficient_funds", "not enough funds")),
    ("authentication_required", _THREE_D_SECURE),
    ("fraud_block", ("fraud", "stolen", "lost card", "pick up card")),
    ("incorrect_details", ("incorrect", "invalid card number", "security code", "cvc")),
    ("generic_decline", ("declined", "do not honor", "do_not_honor")),
)

URGENT_ATTEMPTS = 3
SOON_DAYS = 2
_SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class Reason:
    klass: str
    recoverable: str
    angle: str


def classify(decline_code: str | None, failure_message: str | None) -> Reason:
    code = (decline_code or "").strip().lower()
    message = (failure_message or "").casefold()
    if code in _MEDIUM_DETAILS:
        return Reason("incorrect_details", "medium", DECLINE_TABLE["incorrect_details"][2])
    if code == "card_not_supported" and any(w in message for w in _THREE_D_SECURE):
        return _reason("authentication_required")
    for klass, (codes, _, _) in DECLINE_TABLE.items():
        if code in codes:
            return _reason(klass)
    for klass, words in _KEYWORDS:
        if any(w in message for w in words):
            return _reason(klass)
    return _reason("unknown")


def _reason(klass: str) -> Reason:
    _, recoverable, angle = DECLINE_TABLE[klass]
    return Reason(klass, recoverable, angle)


@dataclass(frozen=True)
class Urgency:
    level: str                     # urgent | soon | scheduled
    days_to_next_attempt: int | None


def urgency(next_attempt_at: str | None, attempt_count: int | None, now: datetime) -> Urgency:
    """`urgent` when no next attempt exists (retries exhausted) or this was attempt 3+; `soon` when the
    next attempt is at most 2 days away; else `scheduled`. Days are whole calendar days from today."""
    nxt = parse_time(next_attempt_at)
    days = None if nxt is None else (nxt.date() - now.date()).days
    if nxt is None or (attempt_count or 0) >= URGENT_ATTEMPTS:
        return Urgency("urgent", days)
    return Urgency("soon" if days <= SOON_DAYS else "scheduled", days)
