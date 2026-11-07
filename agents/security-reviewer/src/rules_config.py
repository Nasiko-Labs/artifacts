"""Shell, Dockerfile, CI, config, generic crypto and diff-aware rules. The diff rules follow the differential
lens: a removed check is high risk whatever the size of the change."""

from diff_parse import DEL
from rules import rule

_CONF = "yaml config docker shell"

RULES = (
    rule("CURL-PIPE-SH", "CWE-494", "high", r"(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b", "",
         "A script is downloaded and run without verification.", "Download, verify a checksum or signature, then run.",
         "config"),
    rule("CHMOD-777", "CWE-732", "medium", r"chmod\s+(?:-R\s+)?0?777\b|chmod\(.*0o?777|\b0o777\b", "",
         "Files are made world-writable.", "Grant the narrowest permissions the process needs.", "files"),
    rule("SHELL-EVAL", "CWE-78", "high", r"^\s*eval\s+\"?\$", "shell", "eval runs a variable as shell code.",
         "Avoid eval; quote variables and call commands directly.", "injection"),
    rule("DOCKER-ADD-URL", "CWE-494", "medium", r"(?i)^\s*ADD\s+https?://", "docker",
         "ADD fetches a remote file without verification.", "Use curl with a checksum check, or COPY a vendored file.",
         "config"),
    rule("DOCKER-USER-ROOT", "CWE-250", "medium", r"^\s*USER\s+(?:root|0)\s*$", "docker",
         "The container runs as root.", "Switch to a non-root USER before CMD.", "config"),
    rule("PRIVILEGED", "CWE-250", "high", r"--privileged\b|privileged\s*:\s*true", "",
         "The container runs privileged, with full host access.", "Drop privileged; add only the capabilities needed.",
         "config"),
    rule("GHA-PR-TARGET", "CWE-829", "medium", r"\bpull_request_target\b", "yaml",
         "pull_request_target runs with secrets on code from forks.",
         "Use pull_request, or never check out or run the PR head under pull_request_target.", "config"),
    rule("GHA-PR-HEAD", "CWE-829", "high", r"ref\s*:\s*\$\{\{\s*github\.event\.pull_request\.head\.(?:sha|ref)", "yaml",
         "The workflow checks out untrusted PR code.", "Do not check out the PR head in a privileged workflow.",
         "config"),
    rule("GHA-WRITE-ALL", "CWE-250", "medium", r"permissions\s*:\s*write-all", "yaml",
         "The workflow token can write everything.", "Grant per-scope permissions (contents: read, ...).", "config"),
    rule("GHA-UNPINNED", "CWE-829", "low", r"uses\s*:\s*(?!actions/|github/|\./|docker://)[\w.-]+/[\w./-]+@"
         r"(?![0-9a-f]{40}\b)\S+", "yaml", "A third-party action is pinned to a tag, not a commit.",
         "Pin the action to a full commit SHA.", "config"),
    rule("GHA-SCRIPT-INJECTION", "CWE-78", "high", r"\$\{\{\s*github\.event\.(?:issue|pull_request|comment|review|"
         r"head_commit)\.[\w.]*(?:title|body|message|ref|label)", "yaml",
         "Untrusted event text is expanded into the workflow.", "Pass it through env: and quote it in the script.",
         "injection"),
    rule("CONFIG-DEBUG", "CWE-489", "medium", r"(?i)^\s*[\"']?debug[\"']?\s*[:=]\s*[\"']?(?:true|1|on|yes)\b", _CONF,
         "Debug mode is on.", "Default debug to off in deployed config.", "config"),
    rule("CORS-ANY", "CWE-942", "medium", r"(?i)allow[-_]?origins?[\"']?\s*[:=]\s*\[?\s*[\"']\*[\"']", "",
         "CORS allows any origin.", "Allowlist the origins that need access.", "network"),
    rule("TLS-OFF-CONFIG", "CWE-295", "high", r"(?i)\b(?:ssl|tls)[-_]?verify\s*[:=]\s*(?:false|0|no)\b|verify_ssl\s*"
         r"[:=]\s*false|insecure[-_]skip[-_]tls[-_]verify\s*[:=]\s*true", "", "TLS verification is turned off.",
         "Keep TLS verification on.", "crypto"),
    rule("SSLMODE-DISABLE", "CWE-319", "medium", r"sslmode=disable", "", "The database connection is not encrypted.",
         "Use sslmode=require (or verify-full).", "crypto"),
    rule("WEAK-CIPHER", "CWE-327", "medium", r"(?i)\b(?:DES|3DES|DESede|RC4|ARC4|Blowfish)\b.{0,40}(?:new|cipher|"
         r"getInstance|encrypt|create)|getInstance\(\s*\"(?:DES|RC4)|\bMODE_ECB\b|/ECB/|createCipheriv\(\s*[\"']"
         r"[\w-]*ecb", "", "A broken cipher or ECB mode is used.", "Use AES-GCM (or ChaCha20-Poly1305) with a random "
         "nonce.", "crypto"),
    rule("STATIC-IV", "CWE-329", "medium", r"(?i)\b(?:iv|nonce)\s*=\s*(?:b?[\"'][^\"']*[\"']|bytes\(\s*\d+\s*\)|"
         r"new byte\[\d+\]|\[\s*0)", "", "The IV/nonce is a constant.", "Generate a fresh random IV/nonce per "
         "message.", "crypto"),
    rule("RSA-SMALL", "CWE-326", "medium", r"(?i)(?:key_size|keysize|modulus|bits)\s*[=:]\s*(?:512|768|1024)\b|"
         r"initialize\(\s*(?:512|768|1024)\s*\)", "", "The RSA key is shorter than 2048 bits.",
         "Use at least 2048-bit RSA (3072 preferred) or an elliptic curve.", "crypto"),
    rule("HARDCODED-KEY", "CWE-321", "high", r"(?i)\b\w*(?:aes|encryption|enc|private|hmac|signing|jwt)_?key\s*=\s*"
         r"b?[\"'][^\"']{8,}[\"']", "", "Key material is hardcoded in source.",
         "Load the key from a secret manager or the environment and rotate it.", "secrets"),
    rule("R-DIFF-REMOVED-CHECK", "CWE-693", "high", r"(?i)authori[sz]|authenticat|permission|\bis_?admin|\bcan_?\w+\(|"
         r"\bhas_?perm|validat|sanitiz|escape|csrf|\bverify|\bcheck_|require_login|login_required|abort\(\s*40[13]|"
         r"forbidden|status\s*=\s*40[13]|\b40[13]\b", "", "A security check was removed in this diff.",
         "Restore the check, or show where the same check now happens.", "auth", on=DEL),
    rule("R-DIFF-SKIP-AUTH", "CWE-693", "low", r"(?i)todo:?\s*(?:add\s+)?auth|skip[_ ]?auth|#\s*nosec|//\s*nosec|"
         r"NOSONAR|noqa:\s*S\d", "", "The change marks a security check as skipped or suppressed.",
         "Implement the check or explain the suppression in review.", "auth"),
)
