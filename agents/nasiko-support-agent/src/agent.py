"""One support turn.

    message → mask secrets → small talk? ─────────────────────────────────────────→ intro
            → known error signature in the text? ─────────────────────────────────→ cause + fix (code only)
            → answering an open troubleshooting question? ────────────────────────→ next clarify / best guess
            → just had a best guess, and this isn't a new Nasiko question? ─────────→ hand-off line
            → plan or pricing question? ──────────────────────────────────────────→ "not in my notes yet"
            → not about Nasiko, or a request to write code? ─────────────────────→ decline
            → troubleshoot, vague? ───────────────────────────────────────────────→ clarify (≤ 2 turns)
            → how-to: BM25 top 3 → model writes from them → render → fence ─────────→ answer
              (a developer-integration question ranks the build/integration docs first, setup flow alongside)

Every reply goes through the fence (unknown UI paths and commands removed) and the secret mask.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import answer
import diagnose
import followup
import intent
import redact
import render
import smalltalk
from fence import Fence
from kb import KnowledgeBase, Section, load
from llm import TurnDeps
from retrieve import Hit, Index

MIN_SCORE = 3.0
MIN_COVERAGE = 0.5
SYMPTOM_FLOWS = {followup.BUILD: "build-failed", followup.STOPPED: "agent-wont-start",
                 followup.CHAT: "agent-wont-start"}
ACT_LEAD = "I can't do that for you (I only explain), but here's how:"

CLARIFY_FIRST = ("Let's narrow it down. What do you see?\n"
                 "1. The upload or build failed\n"
                 "2. The agent shows stopped, failed or crashed\n"
                 "3. It's running, but chat errors or doesn't reply\n"
                 "Reply 1, 2 or 3, or paste the error.")
CLARIFY_EVIDENCE = ("Paste the status line from `nasiko ps`, or the last 50 lines of `nasiko logs my-agent -n 50`, "
                    "and I'll match it.")


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict


class SupportAgent:
    def __init__(self, kb: KnowledgeBase | None = None, store: followup.Store | None = None):
        self._kb = kb or load()
        self._index = Index(self._kb.sections)
        self._fence = Fence(self._kb.facts)
        self._store = store or followup.Store()

    @property
    def kb(self) -> KnowledgeBase:
        return self._kb

    def search(self, text: str, k: int = 3) -> list[Hit]:
        return self._index.search(text, k)

    async def turn(self, raw: str, context_id: str, deps: TurnDeps) -> TurnResult:
        message = intent.read(raw)
        masked = redact.mask(message.text)
        notes = (redact.warning(masked.kinds),) if masked.found else ()
        reply = await self._reply(masked.text, context_id, deps, has_secret=masked.found)
        fenced = self._fence.apply(reply.text)
        final = replace(reply, text=redact.mask(fenced.text).text, removed=reply.removed + fenced.removed)
        text = "\n\n".join([*notes, final.text])
        data = render.envelope(final, notes)
        return TurnResult(render.as_json(data) if message.wants_json else text, data)

    async def _reply(self, text: str, context_id: str, deps: TurnDeps, has_secret: bool) -> render.Reply:
        canned = smalltalk.reply_to(text)
        if canned is not None and not has_secret:
            self._store.clear(context_id)
            return render.Reply(canned, "info")
        signatures = diagnose.match(text)
        if signatures:
            self._store.clear(context_id)
            return self._diagnosis(signatures)
        pending = self._store.get(context_id)
        if pending is not None and pending.guessed:
            # After the best guess, a short "still nothing" gets the hand-off, not the off-topic decline.
            self._store.clear(context_id)
            if not intent.in_scope(text) and not has_secret:
                return render.still_stuck()
            pending = None
        if pending is not None and followup.is_answer(text, intent.looks_like_question(text)):
            return self._continue(pending, text, context_id)
        self._store.clear(context_id)
        if intent.asks_about_plans(text):
            facts = self._kb.facts
            return render.plans_confirmed(facts.plans) if facts.plans_confirmed else render.plans_unconfirmed()
        if intent.asks_for_code(text) or not intent.in_scope(text):
            return render.Reply(smalltalk.OFF_TOPIC_REPLY, "decline", confidence="high")
        hits = self.relevant(text)
        if intent.mode(text) == intent.TROUBLESHOOT and (not hits or not intent.names_feature(text)):
            return self._clarify_start(text, context_id)
        if not hits:
            return render.not_in_notes(self._kb.facts.snapshot_date)
        lead = ACT_LEAD if intent.asks_to_act(text) else ""
        return await answer.compose(text, hits, deps, self._kb.facts.snapshot_date, lead,
                                    prefer_docs=intent.asks_about_integration(text))

    def relevant(self, text: str) -> list[Hit]:
        """The top sections, or none when the best one doesn't cover the question."""
        hits = self._index.search(text, prefer_developer_docs=intent.asks_about_integration(text))
        if not hits or hits[0].score < MIN_SCORE or hits[0].coverage < MIN_COVERAGE:
            return []
        return [h for h in hits if h.score >= MIN_SCORE]

    # --- troubleshooting -------------------------------------------------------------------------

    def _section(self, section_id: str) -> Section | None:
        return self._kb.by_id(section_id)

    def _diagnosis(self, signatures: list[diagnose.Signature]) -> render.Reply:
        return render.troubleshoot(signatures, self._section(signatures[0].flow_id), self._kb.facts.snapshot_date)

    def _clarify_start(self, text: str, context_id: str) -> render.Reply:
        symptom = followup.symptom_of(text)
        if symptom:
            self._store.put(context_id, followup.Pending(asked=1, symptom=symptom))
            return self._symptom_guidance(symptom)
        self._store.put(context_id, followup.Pending(asked=1))
        return render.Reply(CLARIFY_FIRST, "clarify", confidence="low")

    def _continue(self, pending: followup.Pending, text: str, context_id: str) -> render.Reply:
        symptom = pending.symptom or followup.symptom_of(text)
        if pending.asked >= followup.MAX_CLARIFY_TURNS:
            self._store.put(context_id, followup.Pending(asked=pending.asked, symptom=symptom, guessed=True))
            return self._best_guess(symptom)
        self._store.put(context_id, followup.Pending(asked=pending.asked + 1, symptom=symptom))
        if symptom and not pending.symptom:
            return self._symptom_guidance(symptom)
        return render.Reply(CLARIFY_EVIDENCE, "clarify", confidence="low")

    def _symptom_guidance(self, symptom: str) -> render.Reply:
        if symptom == followup.BUILD:
            checklist = [f"- {item}" for item in self._kb.facts.deploy_checklist]
            text = "\n".join([
                "A build usually fails on the zip itself. What the zip needs:", *checklist,
                ("Zip the folder's contents, not the folder. Then open **Fleet → Agents → Builds**, open the "
                 "failed build and paste the last lines of the build log here."),
                "CLI: `nasiko validate` checks the folder before you upload.",
            ])
            return render.Reply(text, "clarify", items=tuple(self._kb.facts.deploy_checklist),
                                where="Fleet → Agents → Builds", cli=("nasiko validate",), confidence="medium")
        if symptom == followup.CHAT:
            text = ("If chat errors or never replies, the agent usually isn't running yet or crashed on start. "
                    "Check its status in **Fleet → Agents**, or run `nasiko logs my-agent -n 100` and paste the "
                    "output here.")
        else:
            text = ("Open the agent from **Fleet → Agents**: if it says **This agent crashed** or **This agent "
                    "failed to deploy**, the error lines are shown there. Or run `nasiko logs my-agent -n 100` and "
                    "paste the output here.")
        return render.Reply(text, "clarify", where="Fleet → Agents", cli=("nasiko logs my-agent -n 100",),
                            confidence="medium")

    def _best_guess(self, symptom: str) -> render.Reply:
        section = self._section(SYMPTOM_FLOWS.get(symptom, "agent-wont-start"))
        lead = "I couldn't match that to a known error. The usual causes and checks:"
        if section is None:
            return render.not_in_notes(self._kb.facts.snapshot_date)
        base = answer.from_section(section, self._kb.facts.snapshot_date, lead)
        text = base.text + f"\nStill stuck? {render.HAND_OFF[0].upper()}{render.HAND_OFF[1:]}, with the log lines."
        return replace(base, text=text, mode="troubleshoot", confidence="low", needs_human=True)
