"""Pure: the checked result of one run (every checked day and currency, its flags and the accounts behind them),
kept in the checkpointer between turns, and how `confidence` and `needs_human` follow from it in code."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from accounts import Behind
from detect import MIN_HISTORY_DAYS, Anomaly, Verdict

VALUE_KEYS = ("mrr_end", "new_mrr", "churned_mrr", "expansion_mrr", "contraction_mrr", "net_new_mrr", "failed_count",
              "failed_amount")


@dataclass(frozen=True)
class Flag:
    anomaly: Anomaly
    behind: Behind

    def to_dict(self) -> dict:
        return {"anomaly": self.anomaly.to_dict(), "behind": self.behind.to_dict()}

    @staticmethod
    def from_dict(data: Mapping) -> Flag:
        return Flag(Anomaly.from_dict(data["anomaly"]), Behind.from_dict(data.get("behind")))


@dataclass(frozen=True)
class DayCheck:
    day: str
    currency: str
    values: Mapping[str, int]
    history_days: int
    mrr_known: bool = True
    flags: tuple[Flag, ...] = ()
    clear: tuple[str, ...] = ()
    not_computed: tuple[str, ...] = ()

    @property
    def enough_history(self) -> bool:
        return self.history_days >= MIN_HISTORY_DAYS

    def to_dict(self) -> dict:
        return {"day": self.day, "currency": self.currency, "values": dict(self.values),
                "history_days": self.history_days, "mrr_known": self.mrr_known,
                "flags": [f.to_dict() for f in self.flags], "clear": list(self.clear),
                "not_computed": list(self.not_computed)}

    @staticmethod
    def from_dict(data: Mapping) -> DayCheck:
        return DayCheck(day=str(data["day"]), currency=str(data["currency"]),
                        values={str(k): int(v) for k, v in (data.get("values") or {}).items()},
                        history_days=int(data.get("history_days") or 0), mrr_known=bool(data.get("mrr_known", True)),
                        flags=tuple(Flag.from_dict(f) for f in data.get("flags") or ()),
                        clear=tuple(str(c) for c in data.get("clear") or ()),
                        not_computed=tuple(str(c) for c in data.get("not_computed") or ()))


def day_check(verdict: Verdict, flags: tuple[Flag, ...]) -> DayCheck:
    m = verdict.metrics
    values = {k: (m.net_new_mrr if k == "net_new_mrr" else getattr(m, k)) for k in VALUE_KEYS}
    return DayCheck(day=verdict.day.isoformat(), currency=verdict.currency, values=values,
                    history_days=verdict.history_days, mrr_known=m.mrr_known, flags=flags, clear=verdict.clear,
                    not_computed=verdict.not_computed)


@dataclass(frozen=True)
class Report:
    checks: tuple[DayCheck, ...]
    baseline_days: int
    source: str = "payments"            # payments | pasted
    livemode: bool | None = None
    changes_known: bool = True
    pages_left_out: bool = False
    unsupported_items: int = 0
    dropped: int = 0
    reconciled: bool = True
    approximate: bool = False           # plan changes older than the 30-day events window were not seen
    notes: tuple[str, ...] = ()
    post: str = ""
    headline_checked: bool = False
    channel: str = ""                   # the channel picked earlier in this conversation (label)
    extra: Mapping[str, str] = field(default_factory=dict)

    @property
    def flags(self) -> tuple[Flag, ...]:
        return tuple(f for c in self.checks for f in c.flags)

    @property
    def confidence(self) -> str:
        if self.source == "pasted" or not self.reconciled:
            return "low"
        partial = (not self.changes_known or self.pages_left_out or self.unsupported_items or self.approximate
                   or self.dropped or any(not c.enough_history for c in self.checks))
        return "medium" if partial else "high"

    @property
    def needs_human(self) -> bool:
        high_bad = any(f.anomaly.severity == "high" and f.anomaly.good_or_bad == "bad" for f in self.flags)
        return high_bad or not self.reconciled or self.pages_left_out

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(Report)}
        return {**data, "checks": [c.to_dict() for c in self.checks], "notes": list(self.notes),
                "extra": dict(self.extra)}

    @staticmethod
    def from_dict(data: Mapping) -> Report:
        names = {f.name for f in fields(Report)}
        plain = {k: v for k, v in data.items() if k in names and k not in ("checks", "notes", "extra")}
        return Report(**{**plain, "checks": tuple(DayCheck.from_dict(c) for c in data.get("checks") or ()),
                         "notes": tuple(str(n) for n in data.get("notes") or ()),
                         "extra": dict(data.get("extra") or {})})
