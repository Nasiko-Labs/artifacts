"""Pure: parse the human `terraform plan` text into Change records.

One block per `# <address> <phrase>` line, until the next such line or a section line (`Plan:`, outputs,
warnings). Attribute lines (`+ a = v`, `- a = v -> null`, `~ a = x -> y # forces replacement`), nested blocks
(addressed `block.attr`), lists and heredocs are read; the verbatim line is kept as evidence.
"""

from __future__ import annotations

import re

from changes import Change, ParsedPlan, split_address
from plan_block import BlockReader

_HEADER = re.compile(r"^\s*# (?P<addr>(?:module|data|[a-z][a-z0-9]*_)[^\s]*\.[^\s]+) (?P<phrase>(?:will|must|has|is) .+?)\s*$")
_PHRASES = (("is tainted, so must be replaced", "replace", "tainted"),
            ("will be destroyed and then created", "replace", ""),
            ("will be created and then destroyed", "replace", "cbd"),
            ("must be replaced", "replace", ""), ("will be created", "create", ""),
            ("will be updated in-place", "update", ""), ("will be destroyed", "delete", ""),
            ("will be read during apply", "read", ""), ("will be imported", "import", ""),
            ("has moved to", "move", ""), ("will no longer be managed by terraform", "forget", ""))
_DRIFT_PHRASES = ("has been deleted", "has changed")
_SECTION = re.compile(r"^\s*(?:Plan: |No changes\.|Changes to Outputs:|─{3,}|-{8,}|Warning:|Note: |"
                      r"Terraform will perform|Terraform used the selected|Saved the plan|Unless you have made|"
                      r"Objects have changed outside|Terraform detected the following)")
_DRIFT_START = re.compile(r"Objects have changed outside of Terraform|Terraform detected the following changes")
_ACTIONS_START = re.compile(r"Terraform will perform the following actions")
_SUMMARY = re.compile(r"^\s*Plan: .*\d+ to (?:add|change|destroy|import)")
_COUNT = re.compile(r"(\d+) to (import|add|change|destroy)")
_WORKSPACE = re.compile(r"(?:^|\s)workspace[\s:\"=]+([A-Za-z0-9_\-]+)", re.IGNORECASE)
_RESOURCE_LINE = re.compile(r'^\s*(?P<sym>-/\+|\+/-|<=|[-+~])?\s*(?:resource|data) "[^"]+" "[^"]+"\s*\{\s*$')
_REASON = re.compile(r"^\s*# \(because (.+?)\)\s*$")


def parse(text: str) -> ParsedPlan:
    lines = text.splitlines()
    changes: list[Change] = []
    drift: list[str] = []
    caveats: list[str] = []
    soft: list[str] = []
    stated, empty, in_drift, truncated = None, False, False, False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _DRIFT_START.search(line):
            in_drift = True
        elif _ACTIONS_START.search(line):
            in_drift = False
        if _SUMMARY.match(line):
            stated = {kind: int(n) for n, kind in _COUNT.findall(line)}
        elif line.strip().startswith("No changes."):
            empty = True
        elif line.strip().startswith("Changes to Outputs:"):
            soft.append("The plan also changes outputs; outputs are not reviewed.")
        elif "Resource targeting is in effect" in line:
            caveats.append("Resource targeting (-target) is in effect: this plan may not show everything.")
        header = _HEADER.match(line)
        if header is None:
            i += 1
            continue
        end = _block_end(lines, i + 1)
        address, phrase = header.group("addr"), header.group("phrase").strip()
        if in_drift or phrase.casefold().startswith(_DRIFT_PHRASES):
            drift.append(f"{address} {phrase}")
        else:
            change, balanced = _change(address, phrase, line.strip(), lines[i + 1:end])
            if change is not None:
                changes.append(change)
            truncated = truncated or (end == len(lines) and not balanced)
        i = end
    if not empty and stated is None and changes:
        truncated = True
    if truncated:
        caveats.append("The plan looks cut off (no complete `Plan:` summary or an unclosed block): "
                       "only what was pasted is reviewed.")
    workspace = _WORKSPACE.search(text)
    return ParsedPlan("human", tuple(changes), tuple(drift), stated, tuple(caveats), tuple(soft), hard=truncated,
                      empty=empty and not changes, workspace=workspace.group(1) if workspace else "",
                      raw_lines=tuple(lines))


def _block_end(lines: list[str], start: int) -> int:
    for j in range(start, len(lines)):
        if _HEADER.match(lines[j]) or _SECTION.match(lines[j]):
            return j
    return len(lines)


def _action(phrase: str) -> tuple[str, str] | None:
    lowered = phrase.casefold()
    for text, action, flag in _PHRASES:
        if lowered.startswith(text):
            return action, flag
    return None


def _change(address: str, phrase: str, header: str, body: list[str]) -> tuple[Change | None, bool]:
    found = _action(phrase)
    if found is None:
        return None, True
    action, flag = found
    module, mode, rtype, _ = split_address(address)
    reason = "tainted" if flag == "tainted" else ""
    cbd = flag == "cbd"
    reader = BlockReader()
    for raw in body:
        because = _REASON.match(raw)
        if because:
            reason = reason or because.group(1)
            continue
        resource = _RESOURCE_LINE.match(raw)
        if resource:
            cbd = cbd or resource.group("sym") == "+/-"
            reader.depth += 1
            continue
        reader.feed(raw)
    change = Change(address=address, resource_type=rtype, action=action, mode=mode, module_path=module,
                    reason=reason, cbd=cbd, attrs=tuple(reader.attrs), before_text="\n".join(reader.before),
                    after_text="\n".join(reader.after), evidence=header)
    return change, reader.depth <= 0
