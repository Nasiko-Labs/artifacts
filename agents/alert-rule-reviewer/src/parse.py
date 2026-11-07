"""Pure: one paste -> `Parsed` (alerts, Alertmanager configs, formats, notes). Each block is detected and
parsed on its own; a block that fails is named in a note and skipped, never guessed."""

from __future__ import annotations

import parse_am
import parse_prom
import parse_vendor
import yamlload
from alerts import Parsed
from detect import Block, Split

MAX_ALERTS = 400


def parse(split: Split) -> Parsed:
    parsed = Parsed(files=dict(split.files))
    for block in split.blocks:
        if block.kind == "hcl":
            parse_vendor.parse_hcl(block.text, block.file, parsed)
        else:
            _data_block(block, parsed)
    if len(parsed.alerts) > MAX_ALERTS:
        extra = len(parsed.alerts) - MAX_ALERTS
        parsed.alerts = parsed.alerts[:MAX_ALERTS]
        parsed.notes.append(f"Only the first {MAX_ALERTS} alerts were reviewed; {extra} more were not checked.")
    return parsed


def _where(block: Block) -> str:
    return f"{block.file} (from line {block.first_line})"


def _data_block(block: Block, parsed: Parsed) -> None:
    stripped = block.text.strip()
    source = "json" if stripped[:1] in "[{" else "yaml"
    try:
        doc = yamlload.load(block.text, block.first_line)
    except ValueError as e:
        doc = _json_fallback(stripped) if source == "json" else None
        if doc is None:
            parsed.skipped.append(f"{_where(block)}: {e}{_template_hint(block.text)}")
            return
    if (parse_prom.parse(doc, block.file, parsed) or parse_am.parse(doc, block.file, parsed)
            or parse_vendor.parse_datadog(doc, block.file, parsed, source)
            or parse_vendor.parse_cfn(doc, block.file, parsed)):
        return
    if doc is not None and not isinstance(doc, str):
        parsed.skipped.append(f"{_where(block)}: not a format I check (Prometheus or Grafana rules, Alertmanager, "
                              "Datadog monitors, CloudWatch alarms)")


def _json_fallback(text: str):
    try:
        return yamlload.load_json(text)
    except ValueError:
        return None


def _template_hint(text: str) -> str:
    if "{{" in text and ("{{-" in text or ".Values" in text or "include " in text or "toYaml" in text):
        return "; it looks templated (Helm), so paste the rendered output instead"
    return ""
