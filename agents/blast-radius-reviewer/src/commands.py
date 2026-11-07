"""Pure: the verification commands shown to the user. Built by code from a token that is verbatim in the
diff, single-quoted for the shell (a `'` inside becomes `'\\''`), at most 80 characters. Nothing is run."""

from __future__ import annotations

MAX_TOKEN_CHARS = 80


def shell_quote(token: str) -> str:
    return "'" + token.replace("'", "'\\''") + "'"


def usable(token: str, diff_text: str) -> bool:
    return bool(token) and len(token) <= MAX_TOKEN_CHARS and "\n" not in token and token in diff_text


def grep(token: str, diff_text: str, where: str = ".") -> str | None:
    if not usable(token, diff_text):
        return None
    return f"grep -rn {shell_quote(token)} {where}"


def for_probe(probe: str, token: str, diff_text: str, detail: str = "", prompt_in_view: bool = False) -> list[str]:
    """The checks for one fired probe, in order; command strings are formatted as code by the renderer."""
    g = grep(token, diff_text)
    checks: list[str | None]
    if probe == "R1":
        checks = [g, "check other repos and docs that call it"]
    elif probe == "R2":
        checks = [g, "check the oldest client build still in use"]
    elif probe == "R3":
        checks = [g, "check rollback: can the previous release run against the migrated schema?",
                  "check existing rows and backfills"]
    elif probe == "R4":
        checks = [g, "check deploy config (CI, Dockerfile, compose, helm) and any second service that reads it"]
    elif probe == "R5":
        restated = ("prompts may restate this rule: check the prompt and agent files in view" if prompt_in_view
                    else "check prompts, agent files and runbooks for the old rule")
        checks = [g, restated]
    elif probe == "R6":
        checks = [grep(f"{token}(", diff_text) or g]
    elif probe == "R7":
        checks = [f"read the changelog for {detail}" if detail else None]
    elif probe == "R8":
        checks = [g, f"list processes that read {token}" if token else None]
    elif probe == "R9":
        checks = [grep(token, diff_text, "tests/")]
    elif probe == "R10":
        checks = [g, "check dashboards, alerts and log parsers that use it"]
    elif probe == "R11":
        checks = ["regenerate and diff"]
    else:
        checks = [g]
    return [c for c in checks if c]
