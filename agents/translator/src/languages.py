"""Pure: the supported target languages, matched in code against names, endonyms and ISO codes.

Codes are matched only right after "to", "into" or "in" (or as a whole answer), so words such as "it" or
"no" in an instruction are never read as Italian or Norwegian. A generic name with several variants picks
a default and says so in a note ("Spanish" is neutral Latin American `es`, "Portuguese" is pt-BR, "Chinese"
is Simplified)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# code, English name, aliases (lowercase names and endonyms), extra codes
_TABLE: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("en", "English", ("english", "inglés", "anglais", "englisch"), ()),
    ("es", "Spanish", ("latin american spanish", "español latino"), ()),
    ("es-MX", "Spanish (Mexico)", ("mexican spanish", "spanish (mexico)"), ()),
    ("es-ES", "Spanish (Spain)", ("spain spanish", "spanish (spain)", "european spanish", "castilian",
                                  "castellano"), ()),
    ("fr", "French", ("french", "français", "francais"), ()),
    ("de", "German", ("german", "deutsch"), ()),
    ("it", "Italian", ("italian", "italiano"), ()),
    ("pt-BR", "Portuguese (Brazil)", ("brazilian portuguese", "portuguese (brazil)", "português brasileiro"), ()),
    ("pt-PT", "Portuguese (Portugal)", ("european portuguese", "portuguese (portugal)"), ()),
    ("nl", "Dutch", ("dutch", "nederlands"), ()),
    ("sv", "Swedish", ("swedish", "svenska"), ()),
    ("no", "Norwegian", ("norwegian", "norsk"), ("nb",)),
    ("da", "Danish", ("danish", "dansk"), ()),
    ("fi", "Finnish", ("finnish", "suomi"), ()),
    ("pl", "Polish", ("polish", "polski"), ()),
    ("cs", "Czech", ("czech", "čeština", "cestina"), ()),
    ("ro", "Romanian", ("romanian", "română", "romana"), ()),
    ("hu", "Hungarian", ("hungarian", "magyar"), ()),
    ("el", "Greek", ("greek", "ελληνικά"), ()),
    ("tr", "Turkish", ("turkish", "türkçe", "turkce"), ()),
    ("ru", "Russian", ("russian", "русский"), ()),
    ("uk", "Ukrainian", ("ukrainian", "українська"), ()),
    ("ar", "Arabic", ("arabic", "العربية"), ()),
    ("he", "Hebrew", ("hebrew", "עברית"), ()),
    ("fa", "Persian", ("persian", "farsi", "فارسی"), ()),
    ("hi", "Hindi", ("hindi", "हिन्दी", "हिंदी"), ()),
    ("bn", "Bengali", ("bengali", "bangla", "বাংলা"), ()),
    ("ta", "Tamil", ("tamil", "தமிழ்"), ()),
    ("ur", "Urdu", ("urdu", "اردو"), ()),
    ("th", "Thai", ("thai", "ไทย"), ()),
    ("vi", "Vietnamese", ("vietnamese", "tiếng việt"), ()),
    ("id", "Indonesian", ("indonesian", "bahasa indonesia"), ()),
    ("ms", "Malay", ("malay", "bahasa melayu"), ()),
    ("fil", "Filipino", ("filipino", "tagalog"), ("tl",)),
    ("ja", "Japanese", ("japanese", "日本語", "nihongo"), ()),
    ("ko", "Korean", ("korean", "한국어"), ()),
    ("zh-Hans", "Chinese (Simplified)", ("simplified chinese", "chinese (simplified)", "简体中文", "mandarin"),
     ("zh-cn",)),
    ("zh-Hant", "Chinese (Traditional)", ("traditional chinese", "chinese (traditional)", "繁體中文"),
     ("zh-tw", "zh-hk")),
)
# Generic names: (alias, code, note stating the default).
_GENERIC = (
    ("spanish", "es", "Used neutral Latin American Spanish; say \"Spain Spanish\" for es-ES."),
    ("español", "es", "Used neutral Latin American Spanish; say \"Spain Spanish\" for es-ES."),
    ("espanol", "es", "Used neutral Latin American Spanish; say \"Spain Spanish\" for es-ES."),
    ("portuguese", "pt-BR", "Used Brazilian Portuguese; say \"European Portuguese\" for pt-PT."),
    ("português", "pt-BR", "Used Brazilian Portuguese; say \"European Portuguese\" for pt-PT."),
    ("chinese", "zh-Hans", "Used Simplified Chinese; say \"traditional Chinese\" for zh-Hant."),
    ("中文", "zh-Hans", "Used Simplified Chinese; say \"traditional Chinese\" for zh-Hant."),
)
_GENERIC_CODES = {"es": None, "pt": ("pt-BR", _GENERIC[3][2]), "zh": ("zh-Hans", _GENERIC[5][2])}
CJK = frozenset({"ja", "ko", "zh"})
_TRADITIONAL_HINT = re.compile(r"\btraditional\b|\btaiwan|\bhong kong\b", re.IGNORECASE)


@dataclass(frozen=True)
class Language:
    code: str
    name: str

    @property
    def base(self) -> str:
        return self.code.split("-")[0].casefold()


@dataclass(frozen=True)
class Match:
    language: Language
    note: str = ""


BY_CODE = {code.casefold(): Language(code, name) for code, name, _, _ in _TABLE}
_ALIASES: dict[str, tuple[str, str]] = {}
for _code, _name, _aliases, _ in _TABLE:
    for _alias in _aliases:
        _ALIASES[_alias] = (_code, "")
for _alias, _code, _note in _GENERIC:
    _ALIASES[_alias] = (_code, _note)
_CODES: dict[str, tuple[str, str]] = {code.casefold(): (code, "") for code, *_ in _TABLE}
for _code, _name, _, _extra in _TABLE:
    _CODES.update({extra: (_code, "") for extra in _extra})
for _short, _choice in _GENERIC_CODES.items():
    if _choice:
        _CODES[_short] = _choice
_SORTED_ALIASES = sorted(_ALIASES, key=len, reverse=True)
_NAME_RE = re.compile(r"(?<![\w])(" + "|".join(re.escape(a) for a in _SORTED_ALIASES) + r")(?![\w])",
                      re.IGNORECASE)
_CODE_RE = re.compile(r"\b(?:to|into|in)\s+(" + "|".join(re.escape(c) for c in sorted(_CODES, key=len, reverse=True))
                      + r")\s*(?=[:,.;!?)\n]|$)", re.IGNORECASE)
_FROM = re.compile(r"\bfrom\s+$", re.IGNORECASE)


def _match(code: str, note: str, text: str) -> Match:
    if code == "zh-Hans" and _TRADITIONAL_HINT.search(text):
        return Match(BY_CODE["zh-hant"])
    return Match(BY_CODE[code.casefold()], note)


def find(text: str) -> tuple[Match | None, Match | None]:
    """(target, source hint) named in an instruction. A name right after "from" is the source."""
    target = source = None
    for found in _NAME_RE.finditer(text or ""):
        code, note = _ALIASES[found.group(1).casefold()]
        if _FROM.search(text[:found.start()]):
            source = source or _match(code, "", text)
        elif target is None:
            target = _match(code, note, text)
    if target is None:
        coded = _CODE_RE.search(text or "")
        if coded:
            target = _match(*_CODES[coded.group(1).casefold()], text)
    return target, source


def strip_names(text: str) -> str:
    """`text` without any language name, endonym or "to <code>" reference."""
    named = _NAME_RE.sub(" ", text or "")
    return _CODE_RE.sub(lambda m: m.group(0)[:m.start(1) - m.start(0)], named)


def from_answer(answer: str) -> Match | None:
    """A whole answer to "which language?": a name, an endonym or a code."""
    plain = " ".join((answer or "").strip().strip(".!").split()).casefold()
    if plain in _CODES:
        return _match(*_CODES[plain], plain)
    target, _ = find(plain)
    return target


def name_for(code: str | None) -> str:
    if not code:
        return "an undetected language"
    base = code.split("-")[0].casefold()
    lang = BY_CODE.get(code.casefold()) or next((v for v in BY_CODE.values() if v.base == base), None)
    return lang.name.split(" (")[0] if lang else code


def prompt_name(code: str) -> str:
    """The target as the model is told it; plain "es" is neutral Latin American Spanish."""
    if code == "es":
        return "Spanish (neutral Latin American)"
    lang = BY_CODE.get(code.casefold())
    return lang.name if lang else code


def is_cjk(code: str | None) -> bool:
    return (code or "").split("-")[0].casefold() in CJK


def supported_names() -> list[str]:
    return [name for _, name, _, _ in _TABLE]
