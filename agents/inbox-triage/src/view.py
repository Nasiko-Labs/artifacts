"""The reply and state shapes shared by the triage and follow-up steps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import render
from models import Checks, Mail, Thread


def reply_only(text: str) -> dict:
    return {"reply": text, "envelope": None, "route": ""}


def shown(threads: Sequence[Thread], meta: render.Meta, checks: Checks, wants_json: bool, lead: str = "") -> dict:
    """The reply and the state for a list the user now sees."""
    env = render.envelope(threads, meta, checks)
    text = render.as_json_text(env) if wants_json else render.render_list(threads, meta, checks, lead)
    return {"reply": text, "envelope": env, "route": "", "threads": [t.to_dict() for t in threads],
            "meta": meta.to_dict(), "checks": checks.to_dict()}


def threads_of(state: Mapping[str, Any]) -> list[Thread]:
    return [Thread.from_dict(t) for t in state.get("threads") or ()]


def mails_of(state: Mapping[str, Any]) -> dict[str, Mail]:
    return {k: Mail.from_dict(v) for k, v in (state.get("mails") or {}).items()}
