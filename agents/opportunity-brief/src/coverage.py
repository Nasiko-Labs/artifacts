"""Pure: one line per source that was not read, and the source names that were, decided from what actually
happened (never from the model's prose). (Pattern copied in from the hardened kit; wording is this agent's.)"""

from __future__ import annotations

from collections.abc import Sequence

CODE_HOST_LINES = {
    "missing": "Code host not connected to this agent, so no repositories were read "
               "(an admin can grant it with `nasiko mcp agent-tools enable`).",
    "unreachable": "The tool gateway wasn't reachable, so no repositories were read.",
    "no_search": "No repository search tool is available to this agent, so no repositories were read.",
    "auth_required": "Your code host connection needs to be connected or re-authorised before I can read it.",
    "approval": "Reading the code host needs an approval under the current policy; set its read tools to allow.",
    "empty": "The code host search returned no repositories I could read.",
}


def unavailable_line(reason: str) -> str:
    return f"Code host search isn't available for your connection ({reason}), so no repositories were read."


def code_host_line(state: str, reason: str = "") -> str:
    return unavailable_line(reason) if state == "unavailable" else CODE_HOST_LINES.get(state, "")


def not_read_lines(urls: Sequence[str]) -> tuple[str, ...]:
    if not urls:
        return ()
    shown = ", ".join(urls[:3]) + (f" and {len(urls) - 3} more" if len(urls) > 3 else "")
    return (f"Not read: {shown}. Paste the text; this agent only reads Hacker News and the code host itself.",)


def sources_label(read: Sequence[str]) -> str:
    names = {"hn": "HN", "code": "code host", "pasted": "pasted"}
    return " + ".join(names[s] for s in ("hn", "code", "pasted") if s in read) or "no sources"
