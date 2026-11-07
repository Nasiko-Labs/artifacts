"""Pure: split a paste into files and blocks, find where the config starts, and read the optional settings
(`scrape_interval`, `owner_label`) from the user's own words.

- `# file: alerts.yml` (or `// file:`) lines start a new file; line numbers are counted per file.
- Lines before the first config-looking line are the user's instruction, not config.
- YAML files split into documents on `---` lines; each document keeps its real line numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import exprs

DEFAULT_FILE = "pasted"
DEFAULT_SCRAPE_SECS = 15
INSTRUCTION_MAX_CHARS = 300

FILE_MARK = re.compile(r"^\s*(?:#|//)\s*file\s*:\s*(\S.*?)\s*$", re.IGNORECASE)
CONFIG_START = re.compile(
    r"^\s*(?:groups\s*:|route\s*:|receivers\s*:|global\s*:|inhibit_rules\s*:|apiVersion\s*:|"
    r"AWSTemplateFormatVersion|Resources\s*:|Type\s*:\s*['\"]?AWS::|resource\s+\"|monitors\s*:|"
    r"[\[{]\s*(?:$|[\"\[{])|---\s*$|-\s+alert\s*:|-\s+name\s*:|rules\s*:)"
)
HCL_RESOURCE = re.compile(r'^\s*resource\s+"(datadog_monitor|aws_cloudwatch_metric_alarm)"\s+"([^"]+)"', re.MULTILINE)
CONFIG_HINT = re.compile(r"\b(?:expr|alert|groups|receivers?|route|query|alarm_name|AlarmName|threshold)\s*[:=]",
                         re.IGNORECASE)
_DOC_SEP = re.compile(r"^---\s*$")
_SCRAPE = re.compile(r"scrape[_ ]interval\D{0,12}?(\d+(?:\.\d+)?(?:ms|s|m|h))\b", re.IGNORECASE)
_OWNER = re.compile(r"owner[_ ]label\s*(?:is|=|:)?\s*[\"'`]?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


@dataclass(frozen=True)
class Block:
    file: str
    text: str            # the block's own text
    first_line: int      # its first line within the file
    kind: str            # hcl | data (YAML or JSON)


@dataclass(frozen=True)
class Split:
    instruction: str
    files: dict[str, str]
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class Settings:
    scrape_s: int = DEFAULT_SCRAPE_SECS
    scrape_assumed: bool = True
    owner_label: str | None = None


def looks_like_config(text: str) -> bool:
    lines = (text or "").splitlines()
    return any(CONFIG_START.match(line) or FILE_MARK.match(line) for line in lines) and bool(
        CONFIG_HINT.search(text) or HCL_RESOURCE.search(text) or re.search(r'"(?:type|query)"\s*:', text))


def split(text: str) -> Split:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if FILE_MARK.match(line) or CONFIG_START.match(line)), len(lines))
    instruction = " ".join(line.strip() for line in lines[:start] if line.strip())[:INSTRUCTION_MAX_CHARS]
    files: dict[str, str] = {}
    blocks: list[Block] = []
    for name, body in _files(lines[start:], len(lines[:start])):
        files[name] = body
        blocks += _blocks(name, body)
    return Split(instruction, files, tuple(blocks))


def _files(lines: list[str], preamble: int) -> list[tuple[str, str]]:
    """(name, text) per file. An unnamed paste keeps the instruction lines blank, so its line numbers
    match the message as pasted."""
    found: list[tuple[str, list[str]]] = []
    for line in lines:
        mark = FILE_MARK.match(line)
        if mark:
            found.append((_unique(mark.group(1), [n for n, _ in found]), []))
        elif found:
            found[-1][1].append(line)
        else:
            found.append((DEFAULT_FILE, [""] * preamble + [line]))
    return [(name, "\n".join(body)) for name, body in found if "".join(body).strip()]


def _unique(name: str, taken: list[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name} ({n})", n + 1
    return candidate


def _blocks(name: str, body: str) -> list[Block]:
    if HCL_RESOURCE.search(body):
        return [Block(name, body, 1, "hcl")]
    blocks, current, first = [], [], 1
    for number, line in enumerate(body.split("\n"), start=1):
        if _DOC_SEP.match(line):
            if "".join(current).strip():
                blocks.append(Block(name, "\n".join(current), first, "data"))
            current, first = [], number + 1
            continue
        current.append(line)
    if "".join(current).strip():
        blocks.append(Block(name, "\n".join(current), first, "data"))
    return blocks


def settings(text: str, instruction: str) -> Settings:
    """Scrape interval and owner label, from the user's words first, then the pasted config."""
    scrape = _SCRAPE.search(instruction) or _SCRAPE.search(text or "")
    secs = exprs.duration_secs(scrape.group(1)) if scrape else None
    owner = _OWNER.search(instruction) or _OWNER.search(text or "")
    return Settings(scrape_s=secs or DEFAULT_SCRAPE_SECS, scrape_assumed=not secs,
                    owner_label=owner.group(1) if owner else None)
