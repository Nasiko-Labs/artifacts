"""Pure: what kind of file a path is (docs, test, ci, build, generated, migration, config, source), and the
code-side reading of changed lines: comments, public symbols, routes, env vars, test commands."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

DOC_EXTS = {".md", ".rst", ".adoc", ".txt"}
DOC_NAMES = re.compile(r"^(?:readme|changelog|changes|license|contributing|authors|notice)\b", re.IGNORECASE)
TEST_PATH = re.compile(r"(?:^|/)(?:tests?|__tests__|spec|specs|testdata)/|(?:^|/)test_[^/]+$|_test\.[a-z]+$|"
                       r"\.(?:test|spec)\.[a-z]+$|Tests?\.(?:java|kt|cs|swift)$|_spec\.rb$")
CI_PATH = re.compile(r"(?:^|/)\.github/|(?:^|/)\.gitlab-ci\.ya?ml$|(?:^|/)Jenkinsfile$|(?:^|/)\.circleci/|"
                     r"(?:^|/)azure-pipelines\.ya?ml$|(?:^|/)\.buildkite/|(?:^|/)\.travis\.ya?ml$")
BUILD_NAMES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "poetry.lock", "pipfile", "pipfile.lock", "setup.py", "setup.cfg", "cargo.toml", "cargo.lock",
    "go.mod", "go.sum", "gemfile", "gemfile.lock", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "makefile", "dockerfile", "uv.lock", "composer.json", "composer.lock", "mix.exs", "mix.lock",
}
GENERATED = re.compile(r"(?:^|/)(?:dist|build|vendor|node_modules|generated|__generated__)/|\.min\.(?:js|css)$|"
                       r"_pb2\.py$|\.pb\.go$|\.snap$|(?:^|/)(?:package-lock\.json|yarn\.lock|pnpm-lock\.yaml|"
                       r"poetry\.lock|cargo\.lock|go\.sum|uv\.lock|gemfile\.lock|composer\.lock)$", re.IGNORECASE)
MIGRATION = re.compile(r"(?:^|/)(?:migrations?|migrate|alembic|db/migrate)/|\.sql$", re.IGNORECASE)
CONFIG = re.compile(r"(?:^|/)\.env[^/]*$|(?:^|/)(?:config|conf|settings)[^/]*/|(?:^|/)(?:config|settings)[^/]*\."
                    r"(?:py|js|ts|ya?ml|toml|json|ini|cfg)$|\.(?:ya?ml|toml|ini|cfg|conf|properties)$", re.IGNORECASE)
SENSITIVE = ("auth", "permission", "crypto", "security", "payment", "billing")

_COMMENT = re.compile(r"^\s*(?:#|//|/\*|\*|--|<!--|\"\"\"|''')")
_DEFS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z]\w*)\s*\("),          # python (public: no leading _)
    re.compile(r"^\s*class\s+([A-Za-z]\w*)"),
    re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|const|let|class|interface|type)\s+(\w+)"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)\s*\("),         # go: exported names are capitalised
    re.compile(r"^\s*pub(?:\(crate\))?\s+(?:async\s+)?(?:fn|struct|enum|trait|const)\s+(\w+)"),
    re.compile(r"^\s*public\s+(?:static\s+)?(?:[\w<>\[\]]+\s+)+(\w+)\s*\("),
)
_ROUTE = re.compile(r"""\.(get|post|put|patch|delete)\(\s*["'](/[^"']*)["']""", re.IGNORECASE)
_ROUTE_ATTR = re.compile(r"""@\w*\.?route\(\s*["'](/[^"']*)["'](?:.*methods=\[\s*["'](\w+))?""", re.IGNORECASE)
_ENV = re.compile(r"""(?:os\.environ(?:\.get)?\(\s*|os\.environ\[\s*|getenv\(\s*|env::var\(\s*|ENV\[\s*)["']"""
                  r"""([A-Z][A-Z0-9_]{2,})["']|process\.env\.([A-Z][A-Z0-9_]{2,})""")
_OPTION = re.compile(r"""(?:add_argument|click\.option|\.option)\(\s*["'](--[\w-]+)["']""")
TEST_COMMAND = re.compile(r"(?:python -m pytest|pytest|npm (?:run )?test|yarn test|pnpm test|go test|cargo test|"
                          r"mvn test|gradle test|\./gradlew test|make test|tox|npx jest|jest|vitest|rspec|"
                          r"bundle exec rspec)\b[^\n`'\"]{0,60}")
_CONCURRENCY = re.compile(r"\b(?:Lock|RLock|Mutex|RwLock|Semaphore|threading|asyncio\.Lock|synchronized|atomic|"
                          r"Atomic\w+|go func|Thread\(|ThreadPool\w*|Condition\(|WaitGroup)\b")
_DESTRUCTIVE_SQL = re.compile(r"\b(?:drop\s+table|drop\s+column|truncate\s+table|delete\s+from|alter\s+table\s+"
                              r"\S+\s+drop)\b", re.IGNORECASE)


def kind_of(path: str) -> str:
    pure = PurePosixPath(path)
    name = pure.name.casefold()
    if CI_PATH.search(path):
        return "ci"
    if TEST_PATH.search(path):
        return "test"
    if GENERATED.search(path):
        return "generated"
    if name in BUILD_NAMES or name.startswith("requirements") and name.endswith(".txt"):
        return "build"
    if pure.suffix.casefold() in DOC_EXTS or path.startswith("docs/") or "/docs/" in path or DOC_NAMES.match(name):
        return "docs"
    if MIGRATION.search(path):
        return "migration"
    if CONFIG.search(path):
        return "config"
    return "source"


def is_comment_or_blank(line: str) -> bool:
    return not line.strip() or bool(_COMMENT.match(line))


def logic_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if not is_comment_or_blank(line)]


def definitions(lines: list[str]) -> dict[str, str]:
    """Public symbol name -> the defining line (stripped), for the given changed lines."""
    found: dict[str, str] = {}
    for line in lines:
        for pattern in _DEFS:
            match = pattern.match(line)
            if match and not match.group(1).startswith("_"):
                found.setdefault(match.group(1), line.strip())
                break
    return found


def routes(lines: list[str]) -> list[str]:
    """`METHOD /path` for each route registered in the lines."""
    out: list[str] = []
    for line in lines:
        for method, path in _ROUTE.findall(line):
            out.append(f"{method.upper()} {path}")
        for path, method in _ROUTE_ATTR.findall(line):
            out.append(f"{(method or 'GET').upper()} {path}")
    return list(dict.fromkeys(out))


def env_vars(lines: list[str]) -> list[str]:
    return list(dict.fromkeys(a or b for line in lines for a, b in _ENV.findall(line)))


def options(lines: list[str]) -> list[str]:
    return list(dict.fromkeys(o for line in lines for o in _OPTION.findall(line)))


def test_commands(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).strip().rstrip(".,;:") for m in TEST_COMMAND.finditer(text or "")))


def has_concurrency(lines: list[str]) -> bool:
    return any(_CONCURRENCY.search(line) for line in lines)


def has_destructive_sql(lines: list[str]) -> bool:
    return any(_DESTRUCTIVE_SQL.search(line) for line in lines)


def sensitive_word(path: str) -> str | None:
    low = path.casefold()
    return next((word for word in SENSITIVE if word in low), None)
