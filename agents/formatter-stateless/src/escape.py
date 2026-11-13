"""Pure: escaping for each target, the matching un-escaping (used only by the fidelity check), and the
link rule: only http, https and mailto URLs found in the data are ever linked."""

from __future__ import annotations

import html
import re

_MD_SPECIALS = re.compile(r"([\\`*_\[\]<>|~])")
_MD_LINE_START = re.compile(r"^(\s*)(#)", re.MULTILINE)
_MD_UNESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_SAFE_URL = re.compile(r"(?:https?://[^\s<>\"'|`]+|mailto:[^\s<>\"'|`]+)", re.IGNORECASE)
_SLACK_FORMATTING = re.compile(r"[*_~]")
_TAG = re.compile(r"<[^>]*>")
_BACKTICK_RUN = re.compile(r"`+")


def safe_url(value: str) -> bool:
    """True when the whole value is one http(s)/mailto URL that can be linked as is."""
    return isinstance(value, str) and _SAFE_URL.fullmatch(value.strip()) is not None and value == value.strip()


# --- Markdown ---------------------------------------------------------------------------------

def md(text: str) -> str:
    return _MD_LINE_START.sub(r"\1\\\2", _MD_SPECIALS.sub(r"\\\1", text))


def md_inline(text: str) -> str:
    """Escaped and on one line (bullets, table cells, key/value lines): newlines become '; '."""
    return md("; ".join(line.strip() for line in text.splitlines() if line.strip()) if "\n" in text else text)


def md_code(text: str, in_table: bool = False) -> str:
    """An inline code span that holds any text (the fence is longer than any backtick run inside)."""
    longest = max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)
    fence = "`" * (longest + 1)
    body = text.replace("|", "\\|") if in_table else text
    return f"{fence} {body} {fence}"


def md_unescape(text: str) -> str:
    return _MD_UNESCAPE.sub(r"\1", text)


# --- Slack mrkdwn -----------------------------------------------------------------------------

def slack_entities(text: str) -> str:
    """Slack's three control characters. Applied to all data text, so `<!channel>`, `<@U1>` or
    `<https://x|y>` in data can never ping anyone or disguise a link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack(text: str) -> str:
    """Data text for mrkdwn: entities escaped; text with formatting characters goes in a code span so
    it renders literally (a backtick inside makes that impossible; the text is then left as is)."""
    escaped = slack_entities(text)
    if _SLACK_FORMATTING.search(text) and "`" not in text and "\n" not in text:
        return f"`{escaped}`"
    return escaped


def slack_unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# --- HTML -------------------------------------------------------------------------------------

def html_text(text: str) -> str:
    return html.escape(text, quote=True)


def html_unescape(markup: str) -> str:
    return html.unescape(_TAG.sub("\n", markup))
