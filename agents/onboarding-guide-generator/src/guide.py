"""The guide as data: what code gathered and checked, stored in the checkpointer so follow-ups
("commands only", "shorter", "who owns src/billing") answer from it with no tool or LLM call.
Nested items are plain JSON-safe dicts; `to_dict` / `from_dict` round-trip it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class Guide:
    repo: str
    hire: str = ""
    role: str = ""
    mode: str = "live"                       # live | pasted
    description: str = ""
    welcome: str = ""
    readme_path: str = ""
    commands: tuple[dict, ...] = ()          # {source, heading, lines}
    tasks: tuple[dict, ...] = ()             # {source, name, definition}
    repo_map: tuple[dict, ...] = ()          # {path, note}
    ticket_asked: str = ""
    ticket: dict | None = None               # {key, title, link}
    keywords: tuple[str, ...] = ()
    start_files: tuple[dict, ...] = ()       # {path, hits}
    owners: tuple[dict, ...] = ()            # {who, kind, detail}
    codeowners_path: str = ""
    rules: tuple[dict, ...] = ()             # {pattern, owners}
    prs: tuple[dict, ...] = ()               # {number, title, author, merged, link, lesson}
    recent_prs: tuple[dict, ...] = ()        # every merged PR read in the window, for "who owns"
    docs: tuple[dict, ...] = ()              # {title, link, where}
    checklist: tuple[str, ...] = ()
    coverage: tuple[str, ...] = ()
    truncated: tuple[str, ...] = ()
    read: dict = field(default_factory=dict)  # readme, docs, owners, ticket -> bool
    dropped: int = 0
    partial: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Guide:
        known = {f.name for f in fields(Guide)}
        return Guide(**{k: tuple(v) if isinstance(v, list) else v for k, v in data.items() if k in known})

    @property
    def no_run_instructions(self) -> bool:
        return not self.commands and not self.tasks
