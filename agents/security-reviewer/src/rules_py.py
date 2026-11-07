"""Python rules (also the SQL and crypto rules every language shares). Concepts follow the differential,
insecure-defaults and sharp-edges lenses (paraphrased from trailofbits/skills, CC-BY-SA; no text copied)."""

from rules import rule

_REQ = r"request\.(?:args|form|values|files|json|GET|POST|data|get_json|query_params|path_params|cookies|headers)"
_LITERAL_ARG = r"\(\s*[\"'][^\"'{}%+]*[\"']\s*[,)]"

RULES = (
    rule("SQLI-FSTRING", "CWE-89", "high", r"\.(?:execute|executemany|raw|query|exec_driver_sql)\(\s*f[\"']|"
         r"\btext\(\s*f[\"']", "py", "SQL is built with an f-string and passed to the database driver.",
         "Use a parameterised query and pass values separately.", "injection"),
    rule("SQLI-FSTRING-VAR", "CWE-89", "high", r"(?i)\bf[\"']\s*(?:select\b|insert\s+into\b|update\s+\w+\s+set\b|"
         r"delete\s+from\b)[^\"'\n]*\{", "py", "A SQL statement is built with an f-string.",
         "Use a parameterised query and pass values separately.", "injection"),
    rule("SQLI-FORMAT", "CWE-89", "high", r"\.(?:execute|executemany|raw|query)\(\s*[\"'][^\"']*[\"']\s*(?:%\s*[\w(]|"
         r"\.format\()", "", "SQL is built with string formatting before it reaches the driver.",
         "Use a parameterised query and pass values separately.", "injection"),
    rule("SQLI-CONCAT", "CWE-89", "high", r"(?i)[\"']\s*(?:select\b|insert\s+into\b|update\s+\w+\s+set\b|delete\s+from\b)"
         r"[^\"'\n]*[\"']\s*\+\s*[\w(]|[\"'][^\"'\n]*\bwhere\b[^\"'\n]*[\"']\s*\+\s*[\w(]", "",
         "SQL is built by string concatenation.", "Use a parameterised query or prepared statement.", "injection"),
    rule("CMD-SHELL-TRUE", "CWE-78", "high", r"subprocess\.\w+\(.*shell\s*=\s*True", "py",
         "A command runs through the shell with a non-literal command string.",
         "Pass an argument list without shell=True, and validate any user value against an allowlist.",
         "injection", unless=r"subprocess\.\w+" + _LITERAL_ARG),
    rule("CMD-OS-SYSTEM", "CWE-78", "high", r"\bos\.(?:system|popen)\(", "py",
         "A command string is passed to the shell.", "Use subprocess.run with an argument list and no shell.",
         "injection", unless=r"os\.(?:system|popen)" + _LITERAL_ARG),
    rule("CODE-EVAL", "CWE-95", "high", r"(?<![.\w])(?:eval|exec)\(", "py js php",
         "eval/exec runs a string as code.", "Remove eval; parse the value (for example json.loads or "
         "ast.literal_eval) or map allowed inputs to functions.", "injection", unless=r"(?:eval|exec)" + _LITERAL_ARG),
    rule("SSTI", "CWE-1336", "high", r"render_template_string\(\s*(?![\"'])|\bTemplate\(\s*(?![\"')])", "py",
         "A template is compiled from a non-literal string.",
         "Render a fixed template file and pass the user value as a variable.", "injection"),
    rule("DESER-PICKLE", "CWE-502", "high", r"\b(?:pickle|cPickle|dill|marshal)\.loads?\(|\bshelve\.open\(", "py",
         "Untrusted bytes deserialised with pickle/marshal can run arbitrary code.",
         "Use a data-only format such as JSON and validate it.", "deserialization"),
    rule("DESER-YAML", "CWE-502", "high", r"\byaml\.(?:load|load_all|unsafe_load)\(", "py",
         "yaml.load without SafeLoader can construct arbitrary objects.", "Use yaml.safe_load.", "deserialization",
         unless=r"Loader\s*=\s*(?:yaml\.)?C?SafeLoader"),
    rule("XXE-LXML", "CWE-611", "high", r"resolve_entities\s*=\s*True", "py",
         "The XML parser resolves external entities.", "Set resolve_entities=False or use defusedxml.", "parsing"),
    rule("JWT-NO-VERIFY", "CWE-347", "critical", r"jwt\.decode\(.*verify\s*=\s*False|[\"']verify_signature[\"']\s*:"
         r"\s*False|algorithms\s*=\s*\[\s*[\"']none[\"']", "py js",
         "JWT signatures are not verified, so anyone can forge a token.",
         "Verify the signature with the expected key and an explicit algorithms list.", "auth"),
    rule("JWT-NO-ALG", "CWE-347", "medium", r"jwt\.decode\((?![^)]*algorithms)[^)]*\)", "py",
         "jwt.decode is called without an explicit algorithms list.",
         "Pass algorithms=[\"RS256\"] (or the one you issue) explicitly.", "auth"),
    rule("TLS-VERIFY-OFF", "CWE-295", "high", r"\bverify\s*=\s*False|ssl\._create_unverified_context|CERT_NONE|"
         r"check_hostname\s*=\s*False", "py", "TLS certificate verification is turned off.",
         "Keep verification on; pin a CA bundle if the server uses a private CA.", "crypto"),
    rule("WEAK-HASH", "CWE-327", "medium", r"hashlib\.(?:md5|sha1)\(|hashlib\.new\(\s*[\"'](?:md5|sha1)", "py",
         "MD5/SHA-1 are broken for signatures and too fast for passwords.",
         "Use SHA-256 for integrity, HMAC for signatures, bcrypt/argon2 for passwords.", "crypto"),
    rule("WEAK-HASH-PASSWORD", "CWE-916", "high", r"(?i)(?:md5|sha1)\W.*passw|passw.*\W(?:md5|sha1)\b", "",
         "A password is hashed with a fast, broken hash.", "Hash passwords with bcrypt, scrypt or argon2.", "crypto"),
    rule("WEAK-RANDOM", "CWE-338", "medium", r"(?i)\b\w*(?:token|secret|passw|reset|otp|nonce|session|api_?key|salt|"
         r"code)\w*\s*=.*\brandom\.(?:random|randint|choice|choices|randrange|getrandbits|sample)\(", "py",
         "A security value is generated with the non-cryptographic random module.",
         "Use secrets.token_urlsafe / secrets.choice.", "crypto"),
    rule("PATH-TRAVERSAL", "CWE-22", "high", r"\b(?:open|send_file|send_from_directory|os\.path\.join|Path|"
         r"FileResponse)\(.*" + _REQ, "py", "A file path is built from request input.",
         "Resolve the path, check it stays under the allowed base directory, or map IDs to files.", "files"),
    rule("TEMPFILE-MKTEMP", "CWE-377", "low", r"tempfile\.mktemp\(", "py", "tempfile.mktemp is race-prone.",
         "Use tempfile.mkstemp or NamedTemporaryFile.", "files"),
    rule("ZIP-SLIP", "CWE-22", "medium", r"\.extractall\(", "py", "An archive is extracted without checking member paths.",
         "Check each member's resolved path stays under the target directory before extracting.", "files"),
    rule("UPLOAD-FILENAME", "CWE-434", "medium", r"\.save\(.*\.filename|os\.path\.join\(.*\.filename", "py",
         "An upload is saved under the client's file name.", "Generate the name server-side (or use secure_filename) "
         "and check the type.", "files", unless=r"secure_filename"),
    rule("SSRF", "CWE-918", "high", r"(?:requests\.(?:get|post|put|delete|head|request)|urlopen|httpx\.\w+)\(.*" + _REQ,
         "py", "An outbound request goes to a URL taken from request input.",
         "Allowlist hosts and schemes, and block internal address ranges.", "network"),
    rule("OPEN-REDIRECT", "CWE-601", "medium", r"redirect\(\s*" + _REQ, "py",
         "The redirect target comes from request input.", "Redirect only to relative paths or an allowlist.", "network"),
    rule("DEBUG-ON", "CWE-489", "medium", r"(?i)\bdebug\s*=\s*True\b", "py",
         "Debug mode is on; tracebacks and (in Flask) an interactive console are exposed.",
         "Read debug from the environment and default it to off.", "config"),
    rule("ALLOWED-HOSTS-ANY", "CWE-183", "medium", r"ALLOWED_HOSTS\s*=\s*\[\s*[\"']\*[\"']", "py",
         "ALLOWED_HOSTS accepts any host header.", "List the real host names.", "config"),
    rule("SECRET-KEY-LITERAL", "CWE-798", "high", r"\bSECRET_KEY\s*=\s*[\"'][^\"']+[\"']|\bsecret_key\s*=\s*[\"']"
         r"[^\"']+[\"']", "py", "The framework secret key is hardcoded in source.",
         "Load it from the environment or a secret manager and rotate it.", "secrets"),
    rule("CSRF-OFF", "CWE-352", "medium", r"@csrf_exempt|WTF_CSRF_ENABLED\s*=\s*False|csrf\s*[:=]\s*(?:False|false)\b",
         "", "CSRF protection is turned off.", "Keep CSRF protection on for cookie-authenticated routes.", "auth"),
    rule("ASSERT-AUTH", "CWE-617", "medium", r"^\s*assert\b.*(?:is_admin|is_authenticated|has_perm|permission|role|"
         r"owner|authori)", "py", "An authorization check uses assert, which python -O removes.",
         "Use an explicit if-check that raises or returns 403.", "auth"),
    rule("FAIL-OPEN", "CWE-636", "medium", r"except(?:\s+[\w.]+(?:\s+as\s+\w+)?)?\s*:\s*return\s+True\b", "py",
         "An error path returns True, so the check fails open.", "Return False (deny) when the check errors.", "auth"),
    rule("DEFAULT-PASSWORD", "CWE-798", "high", r"(?i)\b(?:admin|default|root)_?pass(?:word|wd)?\s*=\s*[\"'][^\"']+"
         r"[\"']", "", "A default or admin password is hardcoded.", "Remove it; require a password set at deploy time.",
         "secrets"),
    rule("TIMING-COMPARE", "CWE-208", "low", r"(?i)\b\w*(?:token|signature|hmac|digest|api_?key)\w*\s*[!=]==?\s*\w*"
         r"(?:token|signature|hmac|digest|key)\b", "", "A secret is compared with ==, which leaks timing.",
         "Use hmac.compare_digest (or crypto.timingSafeEqual).", "auth"),
    rule("COOKIE-FLAGS", "CWE-614", "low", r"set_cookie\(", "py", "A cookie is set without Secure/HttpOnly/SameSite.",
         "Set secure=True, httponly=True and samesite='Lax' (or 'Strict').", "auth",
         unless=r"(?i)secure\s*=\s*True.*httponly|httponly.*secure\s*=\s*True"),
)
