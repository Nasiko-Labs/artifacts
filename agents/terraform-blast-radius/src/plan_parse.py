"""Pure: parse a pasted Terraform plan (the text of `terraform plan`, or `terraform show -json`) into Change
records. The model never decides what a plan will do.

Text: one block per `# <address> <phrase>` header, until the next header or a section line; attribute lines
(`~ key = "old" -> "new"`, `+`, `-`, `(known after apply)`, `# forces replacement`) are read at the resource
block's top level, nested lines count toward the top-level key they sit in. ANSI colour codes are stripped.
"""

from __future__ import annotations

import re

import plan_json
from plan_model import SENSITIVE, UNKNOWN, Attr, Change, ParsedPlan, split_address

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HEADER = re.compile(r"^\s*# (?P<addr>(?:module|data|[a-z][a-z0-9]*_)\S*\.\S+) (?P<phrase>(?:will|must|is|has) .+?)\s*$")
_PHRASES = (("is tainted, so must be replaced", "replace"), ("will be destroyed and then created", "replace"),
            ("will be created and then destroyed", "replace"), ("must be replaced", "replace"),
            ("will be replaced", "replace"), ("will be created", "create"), ("will be updated in-place", "update"),
            ("will be destroyed", "delete"), ("will be read during apply", "read"), ("has moved to", "move"))
_SECTION = re.compile(r"^\s*(?:Plan: |No changes\.|Changes to Outputs:|─{3,}|-{8,}|Warning:|Note: |"
                      r"Terraform will perform|Terraform used the selected|Saved the plan|Objects have changed)")
_SUMMARY = re.compile(r"^\s*Plan: .*?\d+ to (?:add|change|destroy)")
_COUNT = re.compile(r"(\d+) to (add|change|destroy)")
_WORKSPACE = re.compile(r"\bworkspace\b[\s:=\"']+([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_MOVED_FROM = re.compile(r"^\s*# \(moved from (\S+)\)")
_RESOURCE_OPEN = re.compile(r'^\s*(?:-/\+|\+/-|<=|[-+~])?\s*(?:resource|data) "[^"]+" "[^"]+"\s*\{\s*$')
_ATTR = re.compile(r'^\s*(?P<sign>-/\+|\+/-|[-+~])?\s*(?P<key>"[^"]+"|[A-Za-z_][\w-]*)\s*=\s*(?P<val>.*?)\s*$')
_BLOCK = re.compile(r"^\s*(?P<sign>-/\+|\+/-|[-+~])?\s*(?P<key>[A-Za-z_][\w-]*)\s*\{\s*$")
_FORCES = "# forces replacement"
_TEXT_MARKERS = re.compile(r"Terraform will perform the following actions|^\s*# \S+\.\S+ (?:will|must) be|"
                           r"^\s*Plan: \d+ to add|^\s*No changes\.", re.MULTILINE)
MAX_PLAN_CHARS = 100_000


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def looks_like_plan(text: str) -> bool:
    clean = strip_ansi(text)
    data = plan_json.load_object(clean) if "{" in clean else None
    return bool(data is not None and plan_json.is_plan(data)) or bool(_TEXT_MARKERS.search(clean))


def parse(text: str) -> ParsedPlan | None:
    """The plan, or None when the text is not a Terraform plan."""
    clean = strip_ansi(text)[:MAX_PLAN_CHARS]
    data = plan_json.load_object(clean) if "{" in clean else None
    if data is not None and plan_json.is_plan(data):
        return plan_json.parse(data)
    if not _TEXT_MARKERS.search(clean):
        return None
    return parse_text(clean)


def parse_text(text: str) -> ParsedPlan:
    lines = text.splitlines()
    changes: list[Change] = []
    stated: dict[str, int] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if _SUMMARY.match(line):
            stated = {kind: int(n) for n, kind in _COUNT.findall(line)}
        header = _HEADER.match(line)
        if header is None:
            i += 1
            continue
        end = _block_end(lines, i + 1)
        change = _change(header.group("addr"), header.group("phrase"), lines[i + 1:end])
        if change is not None:
            changes.append(change)
        i = end
    workspace = _WORKSPACE.search(text)
    return ParsedPlan("text", tuple(changes), stated, workspace.group(1) if workspace else "")


def _block_end(lines: list[str], start: int) -> int:
    for j in range(start, len(lines)):
        if _HEADER.match(lines[j]) or _SECTION.match(lines[j]):
            return j
    return len(lines)


def _action(phrase: str) -> str | None:
    lowered = phrase.casefold()
    return next((action for text, action in _PHRASES if lowered.startswith(text)), None)


def _change(address: str, phrase: str, body: list[str]) -> Change | None:
    action = _action(phrase)
    if action is None:
        return None
    module, mode, rtype, name = split_address(address)
    reader = _BlockReader()
    moved_from = ""
    for raw in body:
        moved = _MOVED_FROM.match(raw)
        if moved:
            moved_from = moved.group(1)
        elif _RESOURCE_OPEN.match(raw):
            reader.depth = 1
        elif reader.depth >= 1:
            reader.feed(raw)
    if action == "update" and not reader.changed and moved_from:
        action = "move"
    return Change(address=address, rtype=rtype, name=name, module_path=module, mode=mode, action=action,
                  attrs=tuple(reader.attrs()), moved_from=moved_from, known=tuple(reader.known.items()))


class _BlockReader:
    """Reads one resource block's lines; depth 1 is the resource's top level."""

    def __init__(self) -> None:
        self.depth = 0
        self.stack: list[str] = []
        self.changed: dict[str, Attr] = {}
        self.known: dict[str, str] = {}

    def attrs(self) -> list[Attr]:
        return list(self.changed.values())

    def feed(self, raw: str) -> None:
        stripped = raw.strip()
        if not stripped or stripped.startswith("# ("):
            return
        if stripped[0] in "}])" or stripped.startswith(("},", "],")):
            self.depth = max(1, self.depth - 1)
            self.stack = self.stack[: max(0, self.depth - 1)]
            return
        attr, block = _ATTR.match(raw), _BLOCK.match(raw)
        match = attr or block
        sign = (match.group("sign") or "") if match else _sign(stripped)
        key = match.group("key").strip('"') if match else ""
        top = self.stack[0] if self.stack else key
        forces = _FORCES in raw
        if self.depth == 1 and attr is not None:
            self._top_value(key, sign, attr.group("val"), forces)
        elif top and (sign or forces):
            self._mark(top, forces)
        if stripped.endswith(("{", "[", "(")):
            self.stack = [*self.stack, top or key or "?"] if self.depth == 1 else self.stack
            self.depth += 1

    def _top_value(self, key: str, sign: str, value: str, forces: bool) -> None:
        value = value.replace(_FORCES, "").strip()
        if value.endswith(("{", "[", "(")):
            if sign:
                self._mark(key, forces)
            return
        before, after = _values(sign, value)
        plain = after if after not in (None, UNKNOWN, SENSITIVE) else before
        if plain not in (None, UNKNOWN, SENSITIVE):
            self.known.setdefault(key, plain)
        if sign:
            self.changed[key] = Attr(key, before, after, forces)

    def _mark(self, key: str, forces: bool) -> None:
        old = self.changed.get(key)
        self.changed[key] = Attr(key, old.before if old else None, old.after if old else None,
                                 forces or bool(old and old.forces))


def _sign(stripped: str) -> str:
    for sign in ("-/+", "+/-", "~", "+", "-"):
        if stripped.startswith(sign + " "):
            return sign
    return ""


def _values(sign: str, value: str) -> tuple[str | None, str | None]:
    if " -> " in value:
        old, new = value.split(" -> ", 1)
        return _scalar(old), _scalar(new)
    if sign == "+":
        return None, _scalar(value)
    if sign == "-":
        return _scalar(value), None
    return _scalar(value), _scalar(value)


def _scalar(raw: str) -> str | None:
    text = raw.strip().rstrip(",")
    if text in ("null", ""):
        return None
    if text.startswith("(known after apply"):
        return UNKNOWN
    if text.startswith("(sensitive"):
        return SENSITIVE
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text
