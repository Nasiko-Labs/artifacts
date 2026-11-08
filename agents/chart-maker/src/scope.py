"""One turn's inputs and scratch (never persisted): dependencies, the call budget, the parsed request,
and the reply or pause the turn ends with."""

from __future__ import annotations

from dataparse import Table
from hitl import Pending
from inputs import Incoming
from llm import Budget, TurnDeps
from render import Reply


class Scope:
    def __init__(self, deps: TurnDeps, incoming: Incoming, resume: tuple[Pending, str] | None = None):
        self.deps = deps
        self.incoming = incoming
        self.resume = resume
        self.budget = Budget()
        self.as_json = incoming.as_json or bool(resume and resume[0].as_json)
        self.org: tuple[str, ...] = resume[0].org if resume else incoming.org
        self.ask = ""
        self.table: Table | None = None
        self.description = ""
        self.original = ""            # ask + data, kept on a pause so the answer can rebuild the chart
        self.hint: str | None = None
        self.no_more_pauses = resume is not None
        self.reply: Reply | None = None
        self.pending: Pending | None = None
