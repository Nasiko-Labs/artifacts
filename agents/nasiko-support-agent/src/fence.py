"""Pure: the accuracy fence. Every UI path (`**X → Y**`, or a plain `X → Y` chain) and every `nasiko …`
command in a reply is checked against the facts file compiled from the UI labels and `nasiko --help`.
Anything unknown is removed and replaced by a hedge that points at what is known."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from itertools import pairwise

from kb import Facts

ARROW = "→"
UNSURE_MENU = "I'm not sure of the exact menu"
UNSURE_CMD = "I'm not sure of the exact command"
_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_SEGMENT = r"[A-Z(][\w'&()/.:-]*(?: [\w'&()/.:-]+){0,5}"
_PLAIN_CHAIN = re.compile(rf"{_SEGMENT}(?:\s*{ARROW}\s*{_SEGMENT})+")
_CODE_SPAN = re.compile(r"`(\$ )?(nasiko(?:\s[^`\n]*)?)`")
_BARE_CMD = re.compile(r"(?<![`\w/.-])nasiko(?:[ \t]+(?:-{1,2}[\w-]+(?:=[^\s,;`]+)?|<[\w-]+>|[a-z0-9][\w./:-]*))+")
_MD_LINK = re.compile(r"\[[^\]\n]*\]\([^)\s]*\)")
_SKIPPED_SPAN = re.compile(r"\*\*[^*\n]*\*\*|`[^`\n]*`")
_PLACEHOLDER_SEG = re.compile(r"\([a-z][a-z -]*\)")
_ARG_PLACEHOLDER = re.compile(r"<[^>]+>")
# Sidebar items whose page has a different title in the label list.
PAGE_ALIASES = {"Settings": "Organization settings", "Monitor": "TokenOps", "Control": "Budgets",
                "Optimize": "Optimization", "(agent)": "Agent page"}


@dataclass(frozen=True)
class Fenced:
    text: str
    removed: tuple[str, ...]


class Fence:
    def __init__(self, facts: Facts):
        self._facts = facts
        self._paths = tuple(tuple(_split(p)) for p in facts.paths)
        self._groups = {g: set(items) for g, items in facts.sidebar if g}
        self._items = {i for _, items in facts.sidebar for i in items}

    # --- UI paths ---------------------------------------------------------------------------------

    def _is_label(self, seg: str) -> bool:
        return seg in self._facts.labels or seg in self._groups or bool(_PLACEHOLDER_SEG.fullmatch(seg))

    def path_ok(self, path: str) -> bool:
        segs = _split(path)
        if len(segs) < 2:
            return len(segs) == 1 and self._is_label(segs[0])
        if any(known[:len(segs)] == tuple(segs) for known in self._paths):
            return True
        if not all(self._is_label(s) for s in segs):
            return False
        first = segs[0]
        if first not in self._groups and first not in self._items and first not in self._facts.pages:
            return False
        return all(self._step_ok(prev, seg) for prev, seg in pairwise(segs))

    def _step_ok(self, prev: str, seg: str) -> bool:
        if prev in self._groups:
            return seg in self._groups[prev]
        page = PAGE_ALIASES.get(prev, prev)
        children = self._facts.pages.get(page)
        if children is None:
            return True
        return seg in children or seg in self._facts.pages or bool(_PLACEHOLDER_SEG.fullmatch(seg))

    def _known_prefix(self, path: str) -> str:
        segs = _split(path)
        for end in range(len(segs) - 1, 0, -1):
            if self.path_ok(f" {ARROW} ".join(segs[:end])):
                return f" {ARROW} ".join(segs[:end])
        return ""

    def _hedge(self, path: str) -> str:
        prefix = self._known_prefix(path)
        return f"{UNSURE_MENU}; look under **{prefix}**" if prefix else f"{UNSURE_MENU} for this"

    def _fence_bold(self, text: str, removed: list[str]) -> str:
        def sub(m: re.Match) -> str:
            if ARROW not in m.group(1) or self.path_ok(m.group(1)):
                return m.group(0)
            removed.append(m.group(1).strip())
            return self._hedge(m.group(1))
        return _BOLD.sub(sub, text)

    def _fence_plain(self, text: str, removed: list[str]) -> str:
        out = []
        for line in text.split("\n"):
            out.append(self._fence_plain_line(line, removed))
        return "\n".join(out)

    def _fence_plain_line(self, line: str, removed: list[str]) -> str:
        bold_spans = [m.span() for m in _SKIPPED_SPAN.finditer(line)]
        link_spans = [m.span() for m in _MD_LINK.finditer(line)]
        pieces, last = [], 0
        for m in _PLAIN_CHAIN.finditer(line):
            if any(s <= m.start() < e for s, e in bold_spans) or ARROW not in m.group(0):
                continue
            if any(s < m.end() and m.start() < e for s, e in link_spans):
                continue   # link text is the docs' own wording; rewriting it would break the link
            fixed = self._check_chain(m.group(0), removed)
            pieces += [line[last:m.start()], fixed]
            last = m.end()
        return "".join(pieces) + line[last:] if pieces else line

    def _check_chain(self, chain: str, removed: list[str]) -> str:
        segs = [s.strip() for s in chain.split(ARROW)]
        head_words, tail_words = segs[0].split(), segs[-1].split()
        lead = next((i for i in range(len(head_words)) if self._is_label(" ".join(head_words[i:]))), None)
        trail = next((j for j in range(len(tail_words), 0, -1) if self._is_label(" ".join(tail_words[:j]))), None)
        if lead is None:
            lead = _capitalized_run_start(head_words)
        if trail is None:
            trail = _capitalized_run_end(tail_words)
        path = self._chain_path(head_words[lead:], segs[1:-1], tail_words[:trail])
        if self.path_ok(path):
            return chain
        trail = max(trail, _capitalized_run_end(tail_words))   # the unknown label may run on: "Model Store"
        path = self._chain_path(head_words[lead:], segs[1:-1], tail_words[:trail])
        before, after = " ".join(head_words[:lead]), " ".join(tail_words[trail:])
        removed.append(path)
        return " ".join(p for p in (before, self._hedge(path), after) if p)

    @staticmethod
    def _chain_path(head: list[str], middle: list[str], tail: list[str]) -> str:
        return f" {ARROW} ".join([" ".join(head), *middle, " ".join(tail)])

    # --- commands ---------------------------------------------------------------------------------

    def command_problem(self, command: str) -> str | None:
        """None when every command word and flag exists; else the closest known command to point at.
        `nasiko stop/start/restart <agent>` is shorthand for three commands: each must exist."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        alternatives = _slash_alternatives(tokens)
        if alternatives:
            problems = [self._tokens_problem(alt) for alt in alternatives]
            return next((p for p in problems if p is not None), None)
        return self._tokens_problem(tokens)

    def _tokens_problem(self, tokens: list[str]) -> str | None:
        commands = self._facts.commands
        path, idx = "nasiko", 1
        while idx < len(tokens) and f"{path} {tokens[idx]}" in commands:
            path = f"{path} {tokens[idx]}"
            idx += 1
        rest = tokens[idx:]
        has_children = any(c.startswith(path + " ") for c in commands)
        if has_children and rest and not rest[0].startswith("-") and not _ARG_PLACEHOLDER.fullmatch(rest[0]):
            return path
        for tok in rest:
            if tok.startswith("-") and tok != "-" and tok.split("=", 1)[0] not in commands.get(path, frozenset()):
                return path
        return None

    def _replacement(self, known: str) -> str:
        if known == "nasiko":
            return f"({UNSURE_CMD}; see `nasiko --help`)"
        return f"`{known} --help` ({UNSURE_CMD} or flags; check its help)"

    def _bare_problem(self, cmd: str) -> str | None:
        """A bare `nasiko …` in prose is checked only when it looks like a command, not a product mention."""
        words = cmd.split()
        is_command_like = f"nasiko {words[1]}" in self._facts.commands or any(w.startswith("-") for w in words)
        return self.command_problem(cmd) if is_command_like else None

    def unknown_commands(self, line: str) -> list[str]:
        """Every `nasiko …` command in one line (in backticks or bare) that isn't in the CLI snapshot."""
        found = [m.group(2) for m in _CODE_SPAN.finditer(line) if self.command_problem(m.group(2)) is not None]
        rest = _CODE_SPAN.sub("", line)
        found += [c for c in (m.group(0).rstrip(".:,") for m in _BARE_CMD.finditer(rest))
                  if self._bare_problem(c) is not None]
        return found

    def _fence_commands(self, text: str, removed: list[str]) -> str:
        def code(m: re.Match) -> str:
            known = self.command_problem(m.group(2))
            if known is None:
                return m.group(0)
            removed.append(m.group(2))
            return self._replacement(known)
        text = _CODE_SPAN.sub(code, text)

        def bare(m: re.Match) -> str:
            cmd = m.group(0).rstrip(".:,")
            known = self._bare_problem(cmd)
            if known is None:
                return m.group(0)
            removed.append(cmd)
            return self._replacement(known)
        return "\n".join(_BARE_CMD.sub(bare, line) if not _in_code_block(line) else line
                         for line in text.split("\n"))

    # --- all -------------------------------------------------------------------------------------

    def apply(self, text: str) -> Fenced:
        removed: list[str] = []
        out = self._fence_commands(text or "", removed)
        out = self._fence_bold(out, removed)
        out = self._fence_plain(out, removed)
        return Fenced(out, tuple(removed))


_SLASHED = re.compile(r"[a-z][\w-]*(?:/[a-z][\w-]*)+")


def _slash_alternatives(tokens: list[str]) -> list[list[str]]:
    """["nasiko", "stop/start", "<agent>"] → [["nasiko", "stop", "<agent>"], ["nasiko", "start", "<agent>"]]."""
    for i, tok in enumerate(tokens[1:], 1):
        if tok.startswith("-") or _ARG_PLACEHOLDER.fullmatch(tok):
            return []
        if _SLASHED.fullmatch(tok):
            return [[*tokens[:i], alt, *tokens[i + 1:]] for alt in tok.split("/")]
    return []


def _is_capitalized(word: str) -> bool:
    return word[:1].isupper() or word[:1] == "("


def _capitalized_run_start(words: list[str]) -> int:
    """Where the trailing run of capitalized words (the likely unknown label) starts; at least the last word."""
    start = len(words) - 1
    while start > 0 and _is_capitalized(words[start - 1]):
        start -= 1
    return max(start, 0)


def _capitalized_run_end(words: list[str]) -> int:
    """Where the leading run of capitalized words (the likely unknown label) ends; the words after it are
    the rest of the sentence and are kept."""
    end = 1
    while end < len(words) and _is_capitalized(words[end]):
        end += 1
    return end


def _split(path: str) -> list[str]:
    return [s.strip() for s in (path or "").split(ARROW) if s.strip()]


def _in_code_block(line: str) -> bool:
    return line.startswith("    ")
