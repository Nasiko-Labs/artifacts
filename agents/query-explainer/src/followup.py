"""Pure: follow-up commands on the SQL explained earlier in this conversation (no new query pasted).

    "as json" / "json only"                       -> json      (re-render, no call)
    "explain only the CTE `recent`"               -> cte       (one explain call on that CTE)
    "assume orders.customer_id is NOT NULL",
    "index on orders(customer_id)", pasted DDL    -> schema    (re-check in code, no call)
    "rewrite the join as an EXISTS"               -> rewrite   (one call; untested suggestion)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import schema

_JSON = re.compile(r"^\s*(?:show it |give it |return it |output )?(?:as json|json only|in json)\s*[.!]?\s*$",
                   re.IGNORECASE)
_CTE = re.compile(r"\b(?:explain|describe|walk me through)\b.*?\bcte\s+[`\"']?(\w+)", re.IGNORECASE)
_CTE_ALT = re.compile(r"\b(?:explain|describe)\s+(?:only\s+)?(?:the\s+)?[`\"'](\w+)[`\"']\s+cte\b", re.IGNORECASE)
_REWRITE = re.compile(r"\b(?:rewrite|rewritten|re-write|refactor)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str          # json | cte | schema | rewrite
    text: str = ""
    name: str = ""


def parse(text: str) -> Command | None:
    body = (text or "").strip()
    if _JSON.match(body):
        return Command("json")
    cte = _CTE.search(body) or _CTE_ALT.search(body)
    if cte:
        return Command("cte", body, cte.group(1))
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if lines and any(schema.is_hint_line(ln) for ln in lines):
        return Command("schema", body)
    if _REWRITE.search(body):
        return Command("rewrite", body)
    return None
