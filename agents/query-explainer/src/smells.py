"""Pure: every smell rule over one statement, numbered and ordered critical > high > medium > low."""

from __future__ import annotations

import smells_filters
import smells_joins
import smells_misc
from findings import Ctx, Finding, by_severity

MAX_SMELLS_SHOWN = 12


def detect(ctx: Ctx) -> list[Finding]:
    found = smells_misc.detect(ctx) + smells_joins.detect(ctx) + smells_filters.detect(ctx)
    return by_severity(found)
