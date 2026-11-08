"""Pure: is the source an incident transcript (what responders did) or a service description?

Transcript when at least 3 lines look like chat or timestamped lines, or at least 2 lines look like shell
commands; otherwise a description."""

from __future__ import annotations

import re

TRANSCRIPT = "transcript"
DESCRIPTION = "description"
MIN_SPEAKER_LINES = 3
MIN_COMMAND_LINES = 2

_SPEAKER = re.compile(r"^\s*(\[?\d{1,2}:\d{2}|[A-Za-z][\w .-]{1,30}:)")
KNOWN_CLIS = ("kubectl", "docker", "systemctl", "journalctl", "helm", "terraform", "aws", "gcloud", "az", "psql",
              "mysql", "redis-cli", "curl", "dig", "nslookup", "ssh", "git", "ps", "top", "df", "free", "tail",
              "grep", "netstat", "ss", "ping", "sudo")
_CLI = "|".join(re.escape(c) for c in KNOWN_CLIS)
_COMMAND = re.compile(rf"^\s*(?:\$\s+\S|`[^`]+`|(?:{_CLI})\s)|`(?:{_CLI})\s[^`]*`")


def detect(source: str, forced: str | None = None) -> str:
    if forced in (TRANSCRIPT, DESCRIPTION):
        return forced
    lines = (source or "").splitlines()
    speakers = sum(1 for line in lines if _SPEAKER.match(line))
    commands = sum(1 for line in lines if _COMMAND.search(line))
    if speakers >= MIN_SPEAKER_LINES or commands >= MIN_COMMAND_LINES:
        return TRANSCRIPT
    return DESCRIPTION
