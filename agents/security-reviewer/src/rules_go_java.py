"""Go, Java, PHP, Ruby and C# rules."""

from rules import rule

RULES = (
    rule("GO-CMD-SHELL", "CWE-78", "high", r"exec\.Command(?:Context)?\((?:\s*ctx\s*,)?\s*\"(?:sh|bash|/bin/sh|/bin/bash|"
         r"cmd)\"\s*,\s*\"(?:-c|/c)\"\s*,\s*[^\"\s]", "go", "A shell runs a command string built at runtime.",
         "Call the program directly with exec.Command(name, args...) and validate args.", "injection"),
    rule("GO-SQLI", "CWE-89", "high", r"\.(?:Query|Exec|QueryRow)(?:Context)?\((?:\s*ctx\s*,)?\s*(?:fmt\.Sprintf|"
         r"[\"`][^\"`]*[\"`]\s*\+)", "go", "SQL is built with Sprintf or concatenation.",
         "Use placeholders ($1 / ?) and pass values as arguments.", "injection"),
    rule("GO-TLS-SKIP", "CWE-295", "high", r"InsecureSkipVerify\s*:\s*true", "go",
         "TLS certificate verification is turned off.", "Remove InsecureSkipVerify; add the CA to RootCAs.", "crypto"),
    rule("GO-WEAK-RANDOM", "CWE-338", "medium", r"(?i)\b\w*(?:token|secret|passw|otp|nonce|key|salt|code)\w*\s*:?=.*"
         r"\brand\.(?:Int|Intn|Int63|Uint32|Uint64|Read)\(", "go", "A security value may come from math/rand.",
         "Use crypto/rand.", "crypto"),
    rule("GO-PATH", "CWE-22", "high", r"(?:os\.Open|os\.ReadFile|ioutil\.ReadFile|http\.ServeFile|os\.Create)\(.*"
         r"r\.(?:URL|Form|FormValue|PathValue|PostForm)", "go", "A file path is built from request input.",
         "Clean the path and check it stays under the base directory.", "files"),
    rule("GO-SSRF", "CWE-918", "high", r"http\.(?:Get|Post|Head|NewRequest)\(.*r\.(?:URL\.Query|FormValue|PostForm)", "go",
         "An outbound request goes to a URL taken from request input.", "Allowlist hosts and schemes.", "network"),
    rule("JAVA-CMD", "CWE-78", "high", r"Runtime\.getRuntime\(\)\.exec\(.*\+|new ProcessBuilder\(.*\+", "java",
         "A command string is built by concatenation.", "Pass a fixed program and an argument array; validate args.",
         "injection"),
    rule("JAVA-DESER", "CWE-502", "high", r"\.readObject\(\s*\)|new ObjectInputStream\(|XMLDecoder\(", "java",
         "Untrusted data is deserialised with Java serialization.",
         "Use a data-only format, or an ObjectInputFilter allowlist.", "deserialization"),
    rule("JAVA-XXE", "CWE-611", "high", r"(?:DocumentBuilderFactory|SAXParserFactory|XMLInputFactory|"
         r"TransformerFactory)\.newInstance\(", "java",
         "An XML parser factory is created; external entities are on unless disabled.",
         "Set disallow-doctype-decl (or disable external entities) on the factory.", "parsing",
         unless=r"disallow-doctype-decl|FEATURE_SECURE_PROCESSING"),
    rule("JAVA-WEAK-HASH", "CWE-327", "medium", r"MessageDigest\.getInstance\(\s*\"(?:MD5|SHA-?1)\"", "java",
         "MD5/SHA-1 are broken for signatures and too fast for passwords.",
         "Use SHA-256, HMAC, or a password hash (bcrypt/argon2).", "crypto"),
    rule("JAVA-TRUST-ALL", "CWE-295", "high", r"checkServerTrusted\([^)]*\)\s*\{\s*\}|ALLOW_ALL_HOSTNAME_VERIFIER|"
         r"NoopHostnameVerifier|setHostnameVerifier\(\s*\(\s*\w*\s*,\s*\w*\s*\)\s*->\s*true", "java cs",
         "Certificate or host name checks accept everything.", "Use the default trust manager and verifier.",
         "crypto"),
    rule("JAVA-WEAK-RANDOM", "CWE-338", "medium", r"(?i)\b\w*(?:token|passw|otp|key|salt|code)\w*\s*=.*new Random\(",
         "java", "A security value is generated with java.util.Random.", "Use SecureRandom.", "crypto"),
    rule("PHP-CMD", "CWE-78", "high", r"\b(?:system|exec|shell_exec|passthru|popen|proc_open)\(\s*[^\"')]*\$", "php",
         "A shell command includes a variable.", "Use escapeshellarg on each argument, or avoid the shell.",
         "injection"),
    rule("PHP-SQLI", "CWE-89", "high", r"(?i)(?:mysqli?_query|->query|->exec)\(.*\$_(?:GET|POST|REQUEST|COOKIE)|"
         r"(?:->query|mysqli?_query)\(\s*\"[^\"]*\$\w+", "php", "SQL includes request data directly.",
         "Use prepared statements with bound parameters.", "injection"),
    rule("PHP-INCLUDE", "CWE-98", "high", r"\b(?:include|require)(?:_once)?\s*\(?\s*\$_(?:GET|POST|REQUEST|COOKIE)",
         "php", "A file include path comes from request input.", "Map allowed names to fixed files.", "files"),
    rule("PHP-ECHO-XSS", "CWE-79", "medium", r"\becho\s+\$_(?:GET|POST|REQUEST|COOKIE)", "php",
         "Request input is echoed without escaping.", "Escape with htmlspecialchars(..., ENT_QUOTES).", "injection"),
    rule("RUBY-SQLI", "CWE-89", "high", r"\.(?:where|find_by_sql|exec_query|execute)\(\s*\"[^\"]*#\{", "ruby",
         "SQL is built with string interpolation.", "Use bound parameters: where(\"name = ?\", name).", "injection"),
    rule("RUBY-CMD", "CWE-78", "high", r"`[^`]*#\{|\b(?:system|exec|spawn)\(\s*\"[^\"]*#\{|%x\(", "ruby",
         "A shell command interpolates a value.", "Pass the program and arguments separately: system(\"cmd\", arg).",
         "injection"),
    rule("RUBY-DESER", "CWE-502", "high", r"\bMarshal\.load\(|\bYAML\.load\(", "ruby",
         "Untrusted data is deserialised into live objects.", "Use JSON or YAML.safe_load.", "deserialization"),
    rule("CS-SQLI", "CWE-89", "high", r"SqlCommand\(\s*\"[^\"]*\"\s*\+|SqlCommand\(\s*\$\"", "cs",
         "SQL is built with concatenation or interpolation.", "Use SqlParameter / parameterised queries.", "injection"),
    rule("CS-DESER", "CWE-502", "high", r"\bBinaryFormatter\b|TypeNameHandling\.(?:All|Auto|Objects)", "cs",
         "An unsafe .NET deserializer is used.", "Use System.Text.Json without type name handling.",
         "deserialization"),
    rule("CS-TLS-OFF", "CWE-295", "high", r"ServerCertificate(?:Custom)?ValidationCallback\s*=.*=>\s*true", "cs",
         "Certificate validation accepts everything.", "Remove the callback; trust the CA properly.", "crypto"),
)
