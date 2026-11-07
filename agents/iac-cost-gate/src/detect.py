"""Pure: find the IaC in a message, tell its format, and parse it to resources.

    JSON with resource_changes / format_version  -> plan
    JSON or YAML with AWSTemplateFormatVersion or AWS:: resource types -> cfn
    text with resource "..." / module "..." blocks -> hcl

Several files may be pasted, separated by `# file: name` lines; HCL files are parsed together (variables
and locals cross files), anything else per file. Text before the first IaC line is the instruction."""

from __future__ import annotations

import json
import re

import expand
import parse_cfn
import parse_hcl
import parse_plan
import resources as rs

INSTRUCTION_MAX_CHARS = 300
_FILE_LINE = re.compile(r"^\s*#\s*file:\s*(\S.*?)\s*$", re.MULTILINE)
_HCL_LINE = re.compile(r'^\s*(?:resource|variable|locals|provider|terraform|module|data|output)\b\s*(?:"|\{)')
_YAML_LINE = re.compile(r"^\s*(?:AWSTemplateFormatVersion|Resources|Parameters|Description|Transform|Mappings|"
                        r"Conditions|Outputs|Metadata)\s*:")
_FENCE_LINE = re.compile(r"^\s*```[\w-]*\s*$")
_HCL_BLOCK = re.compile(r'^\s*(?:resource|module)\s+"', re.MULTILINE)


def split_request(text: str) -> tuple[str, str]:
    """(instruction, source). Source is "" when no IaC-looking line is found."""
    lines = [ln for ln in (text or "").replace("\r\n", "\n").split("\n") if not _FENCE_LINE.match(ln)]
    for i, line in enumerate(lines):
        if _FILE_LINE.match(line) or _HCL_LINE.match(line) or _YAML_LINE.match(line) or line.lstrip().startswith("{"):
            head = "\n".join(lines[:i]).strip().rstrip(":").strip()
            return head[:INSTRUCTION_MAX_CHARS], "\n".join(lines[i:]).strip()
    return (text or "").strip()[:INSTRUCTION_MAX_CHARS], ""


def chunks(source: str) -> list[tuple[str, str]]:
    """(file name, text) per `# file:` section; one unnamed chunk when there are none."""
    marks = list(_FILE_LINE.finditer(source))
    if not marks:
        return [("", source)]
    out = [("", source[:marks[0].start()])] if source[:marks[0].start()].strip() else []
    for n, mark in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(source)
        out.append((mark.group(1), source[mark.end():end]))
    return out


def format_of(text: str) -> tuple[str, dict | None]:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if parse_plan.is_plan(data):
            return "plan", data
        if parse_cfn.is_template(data):
            return "cfn", data
        return ("", None) if data is not None else (("hcl", None) if _HCL_BLOCK.search(text) else ("", None))
    if _HCL_BLOCK.search(text):
        return "hcl", None
    data = parse_cfn.load(stripped) if _YAML_LINE.search(stripped) else None
    return ("cfn", data) if parse_cfn.is_template(data) else ("", None)


def parse_source(source: str) -> dict | None:
    """The parsed IaC, or None when nothing in it is Terraform, a plan or CloudFormation."""
    parts = [(name, text, *format_of(text)) for name, text in chunks(source)]
    known = [p for p in parts if p[2]]
    if not known:
        return None
    notes = [f"File `{name or 'unnamed'}` is not Terraform, a plan or CloudFormation; skipped."
             for name, text, fmt, _ in parts if not fmt and text.strip()]
    plans = [p for p in known if p[2] == "plan"]
    if plans:
        before, after, more = parse_plan.parse(plans[0][3])
        extra = ["Only the first plan JSON was read."] if len(plans) > 1 or len(known) > 1 else []
        return _parsed("plan", after, before, notes + more + extra)
    hcl_text = "\n".join(text for _, text, fmt, _ in known if fmt == "hcl")
    resources, formats, assumptions = [], [], []
    extra: dict = {"default_tags": {}, "region": "", "multiplied": 0, "unresolved_counts": 0}
    if hcl_text:
        doc = parse_hcl.parse(hcl_text)
        done = expand.expand(doc)
        resources += rs.link(done.resources, {r["address"]: rs.dump(r["attrs"]) for r in done.resources},
                             lambda r: rs.base_address(r["address"]))
        notes += doc.notes
        assumptions += done.assumptions
        formats.append("hcl")
        extra = {"default_tags": done.default_tags, "region": done.region, "multiplied": done.multiplied,
                 "unresolved_counts": done.unresolved_counts, "blocks_failed": doc.blocks_failed}
    for _, _, fmt, data in known:
        if fmt == "cfn":
            found, more = parse_cfn.parse(data)
            resources += found
            notes += more
            formats.append("cfn")
    fmt = formats[0] if len(set(formats)) == 1 else "mixed"
    return _parsed(fmt, resources, None, notes, assumptions, **extra)


def _parsed(fmt: str, resources: list[dict], before: list[dict] | None, notes: list[str],
            assumptions: list[str] | None = None, **extra) -> dict:
    return {"format": fmt, "resources": resources, "before": before, "notes": notes,
            "assumptions": assumptions or [], "default_tags": extra.get("default_tags") or {},
            "region": extra.get("region") or "", "multiplied": extra.get("multiplied", 0),
            "unresolved_counts": extra.get("unresolved_counts", 0), "blocks_failed": extra.get("blocks_failed", 0)}
