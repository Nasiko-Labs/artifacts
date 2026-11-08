"""Pure: the refund rules (first matching rule wins) and the dispute recommendation, on computed facts only.

Rules, in order:
1. not paid / nothing refundable → deny
2. a duplicate charge within 24 h → approve the later one in full
3. an open dispute on this charge → needs_human (never refund a disputed charge)
4. inside the window and at most the auto-approve amount → approve in full
5. inside the window and above it → approve, needs_human ("above the auto-approve amount")
6. annual plan, prorated policy, outside the window → partial (unused share) when used < 90 %, else deny
7. outside the window → deny, unless a goodwill case (no refund in 12 months, tenure >= 365 days and a
   verified customer quote) → needs_human with no refund advice
8. two or more refunds in 12 months → flag "repeat refunds" and needs_human on any approve or partial
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from case import Policy, money
from facts import Facts

USED_RATIO_DENY = 0.90
REPEAT_REFUNDS = 2
GOODWILL_TENURE_DAYS = 365
DECISIONS = ("approve", "partial", "deny", "needs_human")


@dataclass(frozen=True)
class Recommendation:
    decision: str                      # approve | partial | deny | needs_human (refunds); contest |
    amount_minor: int | None = None    # accept_dispute | needs_human (disputes)
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    rule: str = ""
    needs_human: bool = False
    charge_id: str | None = None       # the charge the amount refers to

    def to_dict(self) -> dict:
        return {**asdict(self), "reasons": list(self.reasons), "flags": list(self.flags)}

    @staticmethod
    def from_dict(data: dict | None) -> Recommendation | None:
        if not data:
            return None
        raw = {k: v for k, v in dict(data).items() if k in Recommendation.__dataclass_fields__}
        return Recommendation(**{**raw, "reasons": tuple(raw.get("reasons") or ()),
                                 "flags": tuple(raw.get("flags") or ())})


def recommend(f: Facts, policy: Policy) -> Recommendation:
    if f.charge_id is None:
        return Recommendation("needs_human", reasons=("the charge could not be verified",), rule="insufficient_data",
                              needs_human=True)
    base = _first_rule(f, policy)
    return _repeat_refunds(base, f)


def _first_rule(f: Facts, p: Policy) -> Recommendation:
    cur = f.currency
    refundable = f.refundable_minor or 0
    if not f.paid or refundable == 0:
        return Recommendation("deny", reasons=("nothing to refund: already refunded or unpaid",),
                              rule="nothing_to_refund", charge_id=f.charge_id)
    if f.duplicate_target:
        return Recommendation("approve", f.duplicate_target_minor, (
            (f"duplicate charge: {f.charge_id} and {f.duplicate_of} have the same amount and description within "
            "24 hours; refund the later one"),), rule="duplicate_charge", charge_id=f.duplicate_target)
    if f.open_dispute_id:
        return Recommendation("needs_human", reasons=(
            (f"dispute {f.open_dispute_id} is open on this charge: do not refund a disputed charge (double-loss "
            "risk); resolve it in the dispute"),), rule="open_dispute", needs_human=True, charge_id=f.charge_id)
    days = f.days_since_charge if f.days_since_charge is not None else 10**6
    within = f"{days} days since the charge, inside the {p.window_days}-day refund window"
    if days <= p.window_days and refundable <= p.auto_approve_max_minor:
        return Recommendation("approve", refundable, ((f"{within}; {money(refundable, cur)} is within the "
                                                      f"auto-approve amount {money(p.auto_approve_max_minor, cur)}"),),
                              rule="within_window", charge_id=f.charge_id)
    if days <= p.window_days:
        return Recommendation("approve", refundable, ((f"{within}; above the auto-approve amount "
                                                      f"{money(p.auto_approve_max_minor, cur)}"),),
                              rule="within_window_above_max", needs_human=True, charge_id=f.charge_id)
    outside = f"{days} days since the charge, outside the {p.window_days}-day refund window (ended {f.window_end})"
    if f.plan_interval == "year" and p.annual_refund == "prorated" and f.used_ratio is not None:
        used = f"{round(f.used_ratio * 100)}% of the annual period used ({f.days_used} of {f.period_len_days} days)"
        if f.used_ratio < USED_RATIO_DENY:
            return Recommendation("partial", f.unused_minor, ((f"{outside}; annual plan, policy: prorated; {used}; "
                                                              f"unused share {money(f.unused_minor, cur)}"),),
                                  rule="annual_prorated", charge_id=f.charge_id)
        return Recommendation("deny", reasons=(f"{outside}; annual plan; {used}, at or above 90%",),
                              rule="annual_mostly_used", charge_id=f.charge_id)
    if (f.prior_refund_count_12m == 0 and (f.customer_tenure_days or 0) >= GOODWILL_TENURE_DAYS
            and f.customer_quote):
        return Recommendation("needs_human", reasons=(
            (f"{outside}; goodwill case: no refund in 12 months, customer for {f.customer_tenure_days} days, and "
            "the customer states a reason (quoted in the evidence). A human decides; no refund is recommended"),),
            rule="goodwill_case", needs_human=True, charge_id=f.charge_id)
    return Recommendation("deny", reasons=(outside,), rule="outside_window", charge_id=f.charge_id)


def _repeat_refunds(rec: Recommendation, f: Facts) -> Recommendation:
    if f.prior_refund_count_12m < REPEAT_REFUNDS:
        return rec
    flagged = replace(rec, flags=(*rec.flags, f"repeat refunds: {f.prior_refund_count_12m} in the last 12 months"))
    if rec.decision in ("approve", "partial"):
        return replace(flagged, needs_human=True)
    return flagged


def alternative(rec: Recommendation, decision: str, f: Facts) -> Recommendation:
    """The user's different decision on the same checked facts: approve = the full refundable amount."""
    if decision == rec.decision:
        return rec
    amount = (f.duplicate_target_minor or f.refundable_minor) if decision == "approve" else None
    return Recommendation(decision, amount, (f"your decision: {decision}",), rec.flags, "user_decision",
                          rec.needs_human, rec.charge_id)
