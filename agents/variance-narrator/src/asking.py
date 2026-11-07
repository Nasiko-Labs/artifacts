"""One scoped question about the stored table (or, with no table, the scope check that declines off-topic
asks). The answer stands only when every figure in it is one code formatted and every cause is a supplied
driver; otherwise the reply says the table doesn't say."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import checks
import llm
import prompts
import smalltalk
from digest import NUMBER, norm_number, number_tokens
from present import digest_of, table_text

STATUSES = ("ok", "not_in_input", "off_topic")
MAX_ANSWER_WORDS = 120
NO_TABLE = "(no table has been provided yet)"


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    return {"status": data["status"], "answer": " ".join(str(data.get("answer") or "").split())}


async def ask(deps: llm.TurnDeps, question: str, state: Mapping[str, Any] | None, policy: Sequence[str] = ()) -> str:
    if not deps.traceparent:
        return prompts.NO_TRACE_CONTEXT
    d = digest_of(state) if state else None
    table = table_text(d) if d else NO_TABLE
    messages = prompts.ask_messages(table, d.as_text() if d else NO_TABLE, prompts.strip_markers(question), policy)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.ASK_CONTRACT, parse)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if d is None:
        return smalltalk.NEED_TABLE_REPLY
    source = state.get("source") or ""
    answer = checks.clean_text(parsed["answer"], source)
    if parsed["status"] != "ok" or not answer or checks.words(answer) > MAX_ANSWER_WORDS:
        return prompts.NOT_IN_INPUT
    allowed = d.allowed_numbers() | number_tokens(table) | number_tokens(source)
    supplied = [i.driver for i in d.analysis.items if i.driver]
    if any(norm_number(t) not in allowed for t in NUMBER.findall(answer)) or checks.unsupported_causes(answer, supplied):
        return prompts.NOT_IN_INPUT
    return answer
