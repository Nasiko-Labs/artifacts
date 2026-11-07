"""JavaScript / TypeScript rules (also used for HTML and Vue templates)."""

from rules import rule

_REQ = r"req\.(?:query|body|params|headers|cookies)"

RULES = (
    rule("SQLI-TEMPLATE", "CWE-89", "high", r"\.(?:query|raw|execute|\$queryRawUnsafe|\$executeRawUnsafe)\(\s*`[^`]*"
         r"\$\{", "js", "SQL is built with a template literal.", "Use placeholders ($1 / ?) and pass values separately.",
         "injection"),
    rule("CMD-EXEC-JS", "CWE-78", "high", r"(?:child_process\.|(?<![.\w]))(?:exec|execSync)\(", "js",
         "A command string is run through the shell.", "Use execFile/spawn with an argument array and no shell.",
         "injection", unless=r"(?:exec|execSync)\(\s*([\"'])[^\"'$]*\1\s*[,)]"),
    rule("CODE-FUNCTION", "CWE-95", "high", r"\bnew Function\(|(?<![.\w])Function\(|setTimeout\(\s*[\"'`]|"
         r"setInterval\(\s*[\"'`]", "js", "A string is compiled as code.", "Pass a function, never a string of code.",
         "injection"),
    rule("XSS-SINK", "CWE-79", "medium", r"\.(?:innerHTML|outerHTML)\s*=|dangerouslySetInnerHTML|document\.write\(|"
         r"\bv-html=|\|\s*safe\b|insertAdjacentHTML\(|\.html_safe\b|\{!!.*!!\}", "",
         "HTML is written without escaping.", "Use textContent / framework escaping, or sanitise with a vetted "
         "sanitiser.", "injection"),
    rule("NOSQL-OPERATOR", "CWE-943", "medium", r"\$where|\.find(?:One)?\(\s*" + _REQ + r"|\{\s*[\"']?\$(?:ne|gt|regex|"
         r"in)[\"']?\s*:\s*" + _REQ, "js py", "A NoSQL query takes operators or a whole object from request input.",
         "Cast inputs to plain strings/numbers and build the query object yourself.", "injection"),
    rule("DESER-NODE", "CWE-502", "high", r"require\([\"']node-serialize[\"']\)|\bunserialize\(", "js php",
         "Untrusted data is deserialised into live objects.", "Use JSON.parse on data and validate it.",
         "deserialization"),
    rule("TLS-REJECT-OFF", "CWE-295", "high", r"rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\s*=?\s*"
         r"[\"']?0", "", "TLS certificate verification is turned off.", "Keep verification on; add the private CA "
         "instead.", "crypto"),
    rule("WEAK-HASH-JS", "CWE-327", "medium", r"createHash\(\s*[\"'](?:md5|sha1)[\"']", "js",
         "MD5/SHA-1 are broken for signatures and too fast for passwords.",
         "Use sha256 for integrity, HMAC for signatures, bcrypt/argon2 for passwords.", "crypto"),
    rule("WEAK-RANDOM-JS", "CWE-338", "medium", r"(?i)\b\w*(?:token|secret|passw|reset|otp|nonce|session|key|salt|"
         r"code|id)\w*\s*=.*Math\.random\(", "js", "A security value is generated with Math.random.",
         "Use crypto.randomBytes / crypto.randomUUID.", "crypto"),
    rule("PATH-TRAVERSAL-JS", "CWE-22", "high", r"(?:readFile|readFileSync|createReadStream|sendFile|download|"
         r"path\.join|unlink)\(.*" + _REQ, "js", "A file path is built from request input.",
         "Resolve the path and check it stays under the allowed base directory.", "files"),
    rule("SSRF-JS", "CWE-918", "high", r"(?:fetch|axios(?:\.\w+)?|got|request|https?\.get)\(\s*" + _REQ, "js",
         "An outbound request goes to a URL taken from request input.",
         "Allowlist hosts and schemes, and block internal address ranges.", "network"),
    rule("OPEN-REDIRECT-JS", "CWE-601", "medium", r"res\.redirect\(\s*" + _REQ, "js",
         "The redirect target comes from request input.", "Redirect only to relative paths or an allowlist.",
         "network"),
    rule("CORS-REFLECT", "CWE-942", "medium", r"(?i)origin\s*:\s*(?:true|[\"']\*[\"'])|access-control-allow-origin"
         r"[\"']?\s*[,:]\s*(?:req\.headers\.origin|[\"']\*[\"'])", "",
         "CORS allows any origin (or reflects it).", "Allowlist the origins that need access; never combine * with "
         "credentials.", "network"),
    rule("JWT-IGNORE-EXP", "CWE-613", "medium", r"ignoreExpiration\s*:\s*true", "js",
         "Expired tokens are accepted.", "Remove ignoreExpiration.", "auth"),
    rule("COOKIE-FLAGS-JS", "CWE-1004", "low", r"httpOnly\s*:\s*false|secure\s*:\s*false", "js",
         "A cookie is set without HttpOnly or Secure.", "Set httpOnly: true, secure: true, sameSite: 'lax'.", "auth"),
)
