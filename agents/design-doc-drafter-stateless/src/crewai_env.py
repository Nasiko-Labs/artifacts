"""CrewAI environment defaults. Apply before the first `import crewai`.

The pod's root filesystem is read-only and it has no business phoning home, so CrewAI's
anonymous telemetry, its trace upload prompt and its version check are off, and anything it
stores goes under /tmp. Checked against crewai 1.15.28: `CREWAI_DISABLE_TELEMETRY`
(crewai_core/telemetry.py), `CREWAI_TRACING_ENABLED` (events/listeners/tracing/utils.py),
`CREWAI_DISABLE_VERSION_CHECK` (events/utils/console_formatter.py), `CREWAI_STORAGE_DIR`
(crewai_core/paths.py, appdirs app name; an absolute path wins the join). `HOME` covers the
`~/.crewai`, `~/.config/crewai` and `~/.local/share` paths it also uses. The Dockerfile sets the
same values; these are defaults for local runs and never override an explicit setting.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

CREWAI_ENV_DEFAULTS: dict[str, str] = {
    "CREWAI_DISABLE_TELEMETRY": "true",
    "CREWAI_TRACING_ENABLED": "false",
    "CREWAI_DISABLE_VERSION_CHECK": "true",
    "CREWAI_STORAGE_DIR": "/tmp/crewai",
}


def apply_crewai_env_defaults(env: MutableMapping[str, str] | None = None) -> None:
    target = os.environ if env is None else env
    for key, value in CREWAI_ENV_DEFAULTS.items():
        target.setdefault(key, value)
