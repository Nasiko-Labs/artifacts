"""Pure: the suggested outreach type for an account, by precedence over its signals, and a one-line
angle written in code from the evidence (no model call, so nothing in it can be invented).

    complaint > cancel_scheduled > canceled > downgrade / seat_reduction > past_due / failed payments
              > silent > renewal_near / trial_ending
"""

from __future__ import annotations

from signals import Account

PRECEDENCE: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"complaint"}), "apology_and_fix"),
    (frozenset({"cancel_scheduled"}), "save_call"),
    (frozenset({"canceled"}), "win_back"),
    (frozenset({"downgrade", "seat_reduction"}), "check_in_value"),
    (frozenset({"past_due", "payment_failed_repeatedly"}), "billing_help"),
    (frozenset({"silent"}), "reengage"),
    (frozenset({"renewal_near", "trial_ending"}), "renewal_checkin"),
    (frozenset({"open_high_priority_ticket", "ticket_volume"}), "check_in_value"),
)
LABELS = {"apology_and_fix": "apology and fix", "save_call": "save call", "win_back": "win-back",
          "check_in_value": "value check-in", "billing_help": "billing help", "reengage": "re-engage",
          "renewal_checkin": "renewal check-in"}


def outreach_type(kinds: frozenset[str]) -> str:
    return next((kind for wanted, kind in PRECEDENCE if wanted & kinds), "check_in_value")


def angle(account: Account, kind: str) -> str:
    when = account.effective_dates[0] if account.effective_dates else ""
    if kind == "apology_and_fix":
        subject = f' "{account.complaint_subject}"' if account.complaint_subject else ""
        return f"Acknowledge their email{subject} and offer a short call to fix it."
    if kind == "save_call":
        return f"Ask for a short call before the cancellation takes effect{f' on {when}' if when else ''}."
    if kind == "win_back":
        return "Ask what made them leave and whether anything would bring them back."
    if kind == "billing_help":
        return "Help them update their payment method so the service is not interrupted."
    if kind == "reengage":
        return "Re-open the conversation: they have gone quiet while you kept writing."
    if kind == "renewal_checkin":
        return f"Check in before the renewal{f' on {when}' if when else ''} on how it is going."
    return "Check in on whether the current plan still fits what they need."
