"""Pure: search terms and query tokens from the issue text. No model involved.

Ranked by specificity: paths > error strings > identifiers > route/flag/env tokens > title nouns. The top 6
become code searches; all of them feed BM25 as query tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SEARCHES = 6
MAX_TERM_CHARS = 60
MIN_IDENTIFIER_CHARS = 5

_EXT = r"(?:py|js|jsx|ts|tsx|go|java|rb|rs|cs|php|c|cpp|h|kt|swift|sql|ya?ml|json|toml)"
_PATH = re.compile(rf"(?<![\w/.-])([\w./-]*[\w-]\.{_EXT})\b")
_PY_FRAME = re.compile(r'File "([^"]+)", line \d+')
_JS_FRAME = re.compile(r"\bat [^\s(]+ \(([^():]+):\d+")
_BACKTICK = re.compile(r"`([^`\n]{2,60})`")
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_ERROR = re.compile(r"(?:Error|Exception|panic)\s*:\s*([^\n]{3,200})")
_QUOTED = re.compile(r"[\"']([^\"'\n]{12,120})[\"']")
_ROUTE = re.compile(r"(?<![\w])(/api/[\w/{}:-]+|--[a-z][\w-]+|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b)")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT = re.compile(r"[^A-Za-z0-9]+")

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can cannot could did do does doing done down during each else even ever every few for from further get gets
getting got had has have having he her here hers him his how however i if in into is it its itself just like make
makes may me might more most much must my no nor not now of off often on once only or other our out over own
please per quite rather really same see seems she should since so some such than that the their them then there
these they this those though through to too under until up upon us very via was we were what when where which while
who whom why will with within without would yes yet you your
issue issues bug bugs problem problems error errors exception exceptions fail fails failed failing failure crash
crashes crashed broken break breaks wrong works working work worked expected actual instead happen happens happened
seeing seen getting returns returned return value values true false null none undefined nil line lines file files
traceback stack trace call calls called recent last most first second user users app application code version
step steps reproduce reproduced repro when sometimes always never again still thanks thank hello hi team please
help need want new old try tries tried trying use used using after before today yesterday time times slow fast ok okay
info debug warn warning log logs logged output input data test tests main master branch repo repository
where localize triage look find show tell missing report reported reporter
""".split())  # noqa: SIM905 - a word list reads better as text


@dataclass(frozen=True)
class Terms:
    paths: tuple[str, ...]
    errors: tuple[str, ...]
    identifiers: tuple[str, ...]
    routes: tuple[str, ...]
    nouns: tuple[str, ...]

    @property
    def ranked(self) -> tuple[str, ...]:
        everything = (*self.paths, *self.errors, *self.identifiers, *self.routes, *self.nouns)
        return tuple(dict.fromkeys(t[:MAX_TERM_CHARS].strip() for t in everything if t.strip()))

    @property
    def searches(self) -> tuple[str, ...]:
        """The code searches: basenames for paths (a search engine matches names, not full paths)."""
        terms = [t.rsplit("/", 1)[-1] if t in self.paths else t for t in self.ranked]
        return tuple(dict.fromkeys(terms))[:MAX_SEARCHES]


def extract(title: str, body: str) -> Terms:
    text = f"{title}\n{body}"
    frames = [*_PY_FRAME.findall(text), *_JS_FRAME.findall(text)]
    paths = _clean_paths([*frames, *_PATH.findall(text)])
    errors = tuple(dict.fromkeys(e.strip().rstrip(".") for e in [*_ERROR.findall(text), *_QUOTED.findall(text)]))
    return Terms(paths=paths, errors=errors[:4], identifiers=_identifiers(text, paths),
                 routes=tuple(dict.fromkeys(_ROUTE.findall(text))), nouns=_nouns(title))


def tokens(text: str) -> list[str]:
    """Lowercase word pieces (camelCase and snake_case split), stopwords and 1-char pieces removed."""
    pieces = _SPLIT.split(_CAMEL.sub(" ", text or ""))
    return [_stem(p.casefold()) for p in pieces if len(p) > 1 and p.casefold() not in STOPWORDS and not p.isdigit()]


def _stem(word: str) -> str:
    """A light plural/verb-form strip, so `refunds` matches `refund` and `parsing` matches `parse`."""
    for suffix, keep in (("ies", "y"), ("ing", ""), ("es", ""), ("ed", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4 and not word.endswith("ss"):
            return word[: -len(suffix)] + keep
    return word


def path_tokens(path: str) -> list[str]:
    stem = re.sub(rf"\.{_EXT}$", "", path)
    return [t for t in tokens(stem) if t not in {"src", "lib", "pkg", "internal", "app"}] or tokens(stem)


def _clean_paths(raw: list[str]) -> tuple[str, ...]:
    cleaned = []
    for path in raw:
        p = path.strip().replace("\\", "/")
        for marker in ("/site-packages/", "/node_modules/", "/dist-packages/"):
            if marker in p:
                p = ""
        if p and not p.startswith(("http:", "https:")):
            cleaned.append(p.lstrip("./"))
    return tuple(dict.fromkeys(cleaned))


def _identifiers(text: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    backticked = [b.strip() for b in _BACKTICK.findall(text) if " " not in b.strip() and b.strip() not in paths]
    shaped = [w for w in _IDENT.findall(text) if len(w) >= MIN_IDENTIFIER_CHARS and _code_shaped(w)
              and w.casefold() not in STOPWORDS]
    path_words = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0] for p in paths}
    return tuple(i for i in dict.fromkeys([*backticked, *shaped]) if i not in path_words)


def _code_shaped(word: str) -> bool:
    return "_" in word.strip("_") or bool(_CAMEL.search(word[1:]))


def _nouns(title: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(t for t in tokens(title) if len(t) >= 4))
