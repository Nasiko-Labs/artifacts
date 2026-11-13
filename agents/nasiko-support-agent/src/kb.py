"""The bundled knowledge base: sections (src/kb/sections.jsonl) and facts (src/kb/facts.json), both
compiled at build time from docs.nasiko.com, `nasiko --help` and the UI flows. Read-only at runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent / "kb"
DOCS_HOME = "https://docs.nasiko.com"


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    kind: str                       # flow | doc
    edition: str                    # both | ee | oss
    text: str
    where: str = ""
    cli: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    asked_as: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    good_to_know: str = ""

    @property
    def source_url(self) -> str:
        return self.docs[0] if self.docs else DOCS_HOME

    @staticmethod
    def from_dict(data: dict) -> Section:
        return Section(
            id=data["id"], title=data["title"], kind=data["kind"], edition=data.get("edition", "both"),
            text=data["text"], where=data.get("where", ""), cli=tuple(data.get("cli") or ()),
            docs=tuple(data.get("docs") or ()), asked_as=tuple(data.get("asked_as") or ()),
            steps=tuple(data.get("steps") or ()), good_to_know=data.get("good_to_know") or "",
        )


@dataclass(frozen=True)
class Facts:
    snapshot_date: str
    labels: frozenset[str]
    paths: tuple[str, ...]
    sidebar: tuple[tuple[str, tuple[str, ...]], ...]
    commands: dict[str, frozenset[str]] = field(hash=False)
    docs_urls: frozenset[str] = frozenset()
    plans_status: str = "unconfirmed"
    plans: dict = field(default_factory=dict, hash=False)
    deploy_checklist: tuple[str, ...] = ()
    pages: dict[str, frozenset[str]] = field(default_factory=dict, hash=False)

    @property
    def plans_confirmed(self) -> bool:
        return self.plans_status == "confirmed" and bool(self.plans)

    @staticmethod
    def from_dict(data: dict) -> Facts:
        ui, plans = data.get("ui", {}), data.get("plans", {})
        return Facts(
            snapshot_date=data.get("snapshot_date", ""),
            labels=frozenset(ui.get("labels", [])),
            paths=tuple(ui.get("paths", [])),
            sidebar=tuple((g.get("group", ""), tuple(g.get("items", []))) for g in ui.get("sidebar", [])),
            commands={k: frozenset(v) for k, v in data.get("cli", {}).get("commands", {}).items()},
            docs_urls=frozenset(data.get("docs", {}).get("urls", [])),
            plans_status=plans.get("status") or "unconfirmed",
            plans=dict(plans.get("plans") or {}),
            deploy_checklist=tuple(ui.get("deploy_checklist", [])),
            pages={name: frozenset(items) for name, items in ui.get("pages", {}).items()},
        )


@dataclass(frozen=True)
class KnowledgeBase:
    sections: tuple[Section, ...]
    facts: Facts

    def by_id(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)


@lru_cache(maxsize=1)
def load(kb_dir: Path = KB_DIR) -> KnowledgeBase:
    lines = (kb_dir / "sections.jsonl").read_text(encoding="utf-8").splitlines()
    sections = tuple(Section.from_dict(json.loads(line)) for line in lines if line.strip())
    facts = Facts.from_dict(json.loads((kb_dir / "facts.json").read_text(encoding="utf-8")))
    return KnowledgeBase(sections=sections, facts=facts)
