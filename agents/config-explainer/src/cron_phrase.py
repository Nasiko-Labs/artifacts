"""Pure: a cron schedule in words, templated per field (like cronstrue/crontab.guru), from the raw field
text so lists, ranges and steps read the way they were written."""

from __future__ import annotations

from cron import DAYS, MONTHS, CronSpec

DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _name(token: str, unit: str, aws: bool) -> str:
    upper = token.upper()
    if unit == "day-of-week":
        if upper in DAYS:
            return DAY_NAMES[DAYS.index(upper)]
        value = int(token)
        return DAY_NAMES[(value - 1) % 7] if aws else DAY_NAMES[value % 7]
    if unit == "month":
        return MONTH_NAMES[(MONTHS.index(upper) if upper in MONTHS else int(token) - 1)]
    return token


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _item(item: str, unit: str, aws: bool) -> str:
    body, _, step = item.partition("/")
    every = f"every {_ordinal(int(step))} {unit}" if step and int(step) > 1 else f"every {unit}"
    if body in ("*", "?"):
        return every
    if "-" in body:
        start, end = body.split("-")
        return f"{every} from {_name(start, unit, aws)} through {_name(end, unit, aws)}"
    if step:
        return f"{every} from {_name(body, unit, aws)}"
    return _name(body, unit, aws)


def describe(field: str, unit: str, aws: bool = False) -> str | None:
    """None for "every value"; else the field in words."""
    if field in ("*", "?", "*/1"):
        return None
    items = [_item(i, unit, aws) for i in field.split(",")]
    plain = all(not i.startswith("every") for i in items)
    if unit in ("day-of-week", "month"):
        return _join(items)
    prefix = f"{unit} " if plain else ""
    return prefix + _join(items)


def _time(minute: str, hour: str) -> str:
    if minute.isdigit() and hour.isdigit():
        return f"At {int(hour):02d}:{int(minute):02d}"
    minute_text = describe(minute, "minute") or "every minute"
    hour_text = describe(hour, "hour")
    head = f"At {minute_text}"
    return f"{head} past {hour_text}" if hour_text else head


def phrase(spec: CronSpec) -> str:
    if spec.reboot:
        return "Once, when the cron daemon starts (at boot), not at a time of day."
    if spec.quartz:
        return "A Quartz-style schedule (seconds field or L/W/# syntax); I explain it but don't compute its runs."
    minute, hour, dom, month, dow = spec.fields[:5]
    parts = [_time(minute, hour)]
    dom_text = None if spec.dom_star else describe(dom, "day-of-month")
    dow_text = None if spec.dow_star else describe(dow, "day-of-week", spec.aws)
    if dom_text and dow_text:
        parts.append(f"on {dom_text} or on {dow_text}" if spec.or_rule else f"on {dom_text} and on {dow_text}")
    elif dom_text:
        parts.append(f"on {dom_text}")
    elif dow_text:
        parts.append(f"on {dow_text}")
    month_text = describe(month, "month")
    if month_text:
        parts.append(f"in {month_text}")
    if spec.aws and spec.fields[5] not in ("*", "?"):
        parts.append(f"in {describe(spec.fields[5], 'year')}")
    return " ".join(parts) + "."
