"""Pure: split a paste into labelled kubectl blocks and the user's own words.

A block starts at a command line (`$ kubectl get pods -A`, `user@host:~$ kubectl ...`, `kubectl ...`), at a
recognised table header row, or at a `Name:` line in column 0 (a `describe`). A table ends at a blank line;
a `describe` runs to the next boundary. Tabular lines that belong to no recognised header become an
unrecognised block (listed, never guessed at). Everything else is the user's instruction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_COMMAND = re.compile(r"^\s*(?:\S*[$%#>]\s+)?kubectl\s+(?P<args>\S.*)$")
_HEADER_TOKEN = r"[A-Z][A-Z0-9\-]*(?:\([A-Za-z%]+\)|%)?"
_HEADER = re.compile(rf"{_HEADER_TOKEN}(?:\s+{_HEADER_TOKEN})*")
_NAME_LINE = re.compile(r"^Name:\s+\S")
_TABULAR = re.compile(r"\S\s{2,}\S.*\S\s{2,}\S")
_NO_RESOURCES = re.compile(r"^No resources found", re.IGNORECASE)
_NAMESPACE_FLAG = re.compile(r"(?:-n|--namespace)(?:\s+|=)([a-z0-9][-a-z0-9.]*)")

TABLE_TYPES = ("pods", "nodes", "deployments", "replicasets", "statefulsets", "daemonsets", "events",
               "top_nodes", "top_pods", "rollout_history")
DESCRIBE_TYPES = ("describe_pod", "describe_node")
_COMMAND_TYPES = (
    (r"^get\s+(?:pods?|po)\b", "pods"), (r"^get\s+(?:nodes?|no)\b", "nodes"),
    (r"^get\s+(?:deployments?|deploy)\b", "deployments"), (r"^get\s+(?:replicasets?|rs)\b", "replicasets"),
    (r"^get\s+(?:statefulsets?|sts)\b", "statefulsets"), (r"^get\s+(?:daemonsets?|ds)\b", "daemonsets"),
    (r"^get\s+(?:events?|ev)\b", "events"), (r"^top\s+(?:pods?|po)\b", "top_pods"),
    (r"^top\s+(?:nodes?|no)\b", "top_nodes"), (r"^rollout\s+history\b", "rollout_history"),
    (r"^describe\s+(?:pods?|po)\b", "describe_pod"), (r"^describe\s+(?:nodes?|no)\b", "describe_node"),
)


@dataclass
class Block:
    type: str | None = None          # a TABLE_TYPES / DESCRIBE_TYPES value, or None (not recognised)
    label: str = ""                  # the kubectl command line, when one was pasted
    header: str = ""
    lines: list[str] = field(default_factory=list)
    describe: bool = False

    @property
    def text(self) -> str:
        head = [f"$ kubectl {self.label}"] if self.label else []
        return "\n".join([*head, *([self.header] if self.header else []), *self.lines]).strip("\n")

    @property
    def namespace(self) -> str | None:
        match = _NAMESPACE_FLAG.search(self.label)
        return match.group(1) if match else None

    def first_line(self) -> str:
        return next((ln.strip() for ln in [self.header, *self.lines] if ln.strip()), "")


@dataclass(frozen=True)
class Split:
    blocks: tuple[Block, ...]
    unparsed: tuple[Block, ...]
    instruction: str


def table_type(header: str) -> str | None:
    if not _HEADER.fullmatch(header.strip()):
        return None
    cols = set(re.findall(r"\S+(?: \S+)*", header.strip().upper())) | set(header.upper().split())
    if {"LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"} <= cols:
        return "events"
    if {"REVISION", "CHANGE-CAUSE"} <= cols:
        return "rollout_history"
    if "CPU(CORES)" in cols and "MEMORY(BYTES)" in cols:
        return "top_nodes" if {"CPU%", "CPU(%)"} & cols else "top_pods"
    checks = ((("NAME", "READY", "STATUS", "RESTARTS", "AGE"), "pods"),
              (("NAME", "STATUS", "ROLES", "AGE", "VERSION"), "nodes"),
              (("DESIRED", "CURRENT", "READY", "UP-TO-DATE", "AVAILABLE"), "daemonsets"),
              (("NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"), "deployments"),
              (("NAME", "DESIRED", "CURRENT", "READY", "AGE"), "replicasets"))
    for needed, kind in checks:
        if set(needed) <= cols:
            return kind
    if cols - {"NAMESPACE", "CONTAINERS", "IMAGES", "SELECTOR"} == {"NAME", "READY", "AGE"}:
        return "statefulsets"
    return None


def command_type(args: str) -> str | None:
    return next((kind for pattern, kind in _COMMAND_TYPES if re.search(pattern, args.strip())), None)


def describe_type(block: Block) -> str | None:
    keys = {ln.split(":", 1)[0] for ln in block.lines if ln[:1].isalpha() and ":" in ln}
    if {"Namespace", "Containers"} <= keys:
        return "describe_pod"
    if keys & {"Roles", "Capacity", "Taints", "Unschedulable"} and "Containers" not in keys:
        return "describe_node"
    return None


def split(text: str) -> Split:
    state = _Splitter()
    for line in (text or "").splitlines():
        state.feed(line)
    state.close()
    recognised = tuple(b for b in state.blocks if b.type)
    unparsed = tuple(b for b in state.blocks if not b.type)
    return Split(recognised, unparsed, "\n".join(state.prose).strip())


class _Splitter:
    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.prose: list[str] = []
        self.current: Block | None = None

    def feed(self, line: str) -> None:
        command = _COMMAND.match(line)
        if command:
            self.close()
            self.current = Block(label=command.group("args").strip())
            return
        kind = table_type(line) if line.strip() else None
        if kind:
            fresh = self.current is not None and not self.current.type and not self.current.lines \
                and not self.current.header
            if not fresh:
                self.close()
                self.current = Block()
            self.current.type, self.current.header = kind, line.rstrip()
            return
        if _NAME_LINE.match(line):
            self._start_describe(line)
            return
        self._body(line)

    def _start_describe(self, line: str) -> None:
        cur = self.current
        if not (cur is not None and cur.label and not cur.lines and not cur.header):
            self.close()
            self.current = Block()
        self.current.describe = True
        self.current.lines.append(line.rstrip())

    def _body(self, line: str) -> None:
        cur = self.current
        if cur is None:
            if _TABULAR.search(line):
                self.current = Block(lines=[line.rstrip()])
            else:
                self.prose.append(line)
            return
        if not line.strip():
            if cur.describe or (cur.label and not cur.type and not cur.lines):
                cur.lines.append("")
            else:
                self.close()
            return
        if cur.type in TABLE_TYPES and not re.search(r"\d|\s{2,}", line.strip()):
            self.prose.append(line)  # words typed under a table, not a row
            return
        cur.lines.append(line.rstrip())

    def close(self) -> None:
        cur, self.current = self.current, None
        if cur is None:
            return
        while cur.lines and not cur.lines[-1].strip():
            cur.lines.pop()
        if cur.describe:
            cur.type = describe_type(cur) or (command_type(cur.label) if cur.label else None)
            cur.type = cur.type if cur.type in DESCRIBE_TYPES else None
        elif not cur.type and cur.label and all(_NO_RESOURCES.match(ln.strip()) for ln in cur.lines if ln.strip()):
            cur.type = command_type(cur.label)  # "No resources found": an empty table of that kind
        if not cur.type and not cur.lines and not cur.header:
            if cur.label:
                self.prose.append(f"kubectl {cur.label}")
            return
        self.blocks.append(cur)
