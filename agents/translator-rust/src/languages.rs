//! Pure: the supported target languages, matched in code against names, endonyms and ISO codes.
//!
//! Codes are matched only right after "to", "into" or "in" (or as a whole answer), so words such as "it"
//! or "no" in an instruction are never read as Italian or Norwegian. A generic name with several
//! variants picks a default and says so in a note ("Spanish" is neutral Latin American `es`,
//! "Portuguese" is pt-BR, "Chinese" is Simplified).

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

use crate::util::{collapse_ws, re, strip_chars};

type Row = (
    &'static str,
    &'static str,
    &'static [&'static str],
    &'static [&'static str],
);

/// code, English name, aliases (lowercase names and endonyms), extra codes
const TABLE: &[Row] = &[
    (
        "en",
        "English",
        &["english", "inglés", "anglais", "englisch"],
        &[],
    ),
    (
        "es",
        "Spanish",
        &["latin american spanish", "español latino"],
        &[],
    ),
    (
        "es-MX",
        "Spanish (Mexico)",
        &["mexican spanish", "spanish (mexico)"],
        &[],
    ),
    (
        "es-ES",
        "Spanish (Spain)",
        &[
            "spain spanish",
            "spanish (spain)",
            "european spanish",
            "castilian",
            "castellano",
        ],
        &[],
    ),
    ("fr", "French", &["french", "français", "francais"], &[]),
    ("de", "German", &["german", "deutsch"], &[]),
    ("it", "Italian", &["italian", "italiano"], &[]),
    (
        "pt-BR",
        "Portuguese (Brazil)",
        &[
            "brazilian portuguese",
            "portuguese (brazil)",
            "português brasileiro",
        ],
        &[],
    ),
    (
        "pt-PT",
        "Portuguese (Portugal)",
        &["european portuguese", "portuguese (portugal)"],
        &[],
    ),
    ("nl", "Dutch", &["dutch", "nederlands"], &[]),
    ("sv", "Swedish", &["swedish", "svenska"], &[]),
    ("no", "Norwegian", &["norwegian", "norsk"], &["nb"]),
    ("da", "Danish", &["danish", "dansk"], &[]),
    ("fi", "Finnish", &["finnish", "suomi"], &[]),
    ("pl", "Polish", &["polish", "polski"], &[]),
    ("cs", "Czech", &["czech", "čeština", "cestina"], &[]),
    ("ro", "Romanian", &["romanian", "română", "romana"], &[]),
    ("hu", "Hungarian", &["hungarian", "magyar"], &[]),
    ("el", "Greek", &["greek", "ελληνικά"], &[]),
    ("tr", "Turkish", &["turkish", "türkçe", "turkce"], &[]),
    ("ru", "Russian", &["russian", "русский"], &[]),
    ("uk", "Ukrainian", &["ukrainian", "українська"], &[]),
    ("ar", "Arabic", &["arabic", "العربية"], &[]),
    ("he", "Hebrew", &["hebrew", "עברית"], &[]),
    ("fa", "Persian", &["persian", "farsi", "فارسی"], &[]),
    ("hi", "Hindi", &["hindi", "हिन्दी", "हिंदी"], &[]),
    ("bn", "Bengali", &["bengali", "bangla", "বাংলা"], &[]),
    ("ta", "Tamil", &["tamil", "தமிழ்"], &[]),
    ("ur", "Urdu", &["urdu", "اردو"], &[]),
    ("th", "Thai", &["thai", "ไทย"], &[]),
    ("vi", "Vietnamese", &["vietnamese", "tiếng việt"], &[]),
    ("id", "Indonesian", &["indonesian", "bahasa indonesia"], &[]),
    ("ms", "Malay", &["malay", "bahasa melayu"], &[]),
    ("fil", "Filipino", &["filipino", "tagalog"], &["tl"]),
    ("ja", "Japanese", &["japanese", "日本語", "nihongo"], &[]),
    ("ko", "Korean", &["korean", "한국어"], &[]),
    (
        "zh-Hans",
        "Chinese (Simplified)",
        &[
            "simplified chinese",
            "chinese (simplified)",
            "简体中文",
            "mandarin",
        ],
        &["zh-cn"],
    ),
    (
        "zh-Hant",
        "Chinese (Traditional)",
        &["traditional chinese", "chinese (traditional)", "繁體中文"],
        &["zh-tw", "zh-hk"],
    ),
];
const SPANISH_NOTE: &str = "Used neutral Latin American Spanish; say \"Spain Spanish\" for es-ES.";
const PORTUGUESE_NOTE: &str = "Used Brazilian Portuguese; say \"European Portuguese\" for pt-PT.";
const CHINESE_NOTE: &str = "Used Simplified Chinese; say \"traditional Chinese\" for zh-Hant.";
/// Generic names: (alias, code, note stating the default).
const GENERIC: &[(&str, &str, &str)] = &[
    ("spanish", "es", SPANISH_NOTE),
    ("español", "es", SPANISH_NOTE),
    ("espanol", "es", SPANISH_NOTE),
    ("portuguese", "pt-BR", PORTUGUESE_NOTE),
    ("português", "pt-BR", PORTUGUESE_NOTE),
    ("chinese", "zh-Hans", CHINESE_NOTE),
    ("中文", "zh-Hans", CHINESE_NOTE),
];
const CJK: &[&str] = &["ja", "ko", "zh"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Language {
    pub code: &'static str,
    pub name: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Match {
    pub language: Language,
    pub note: &'static str,
}

static BY_CODE: LazyLock<HashMap<String, Language>> = LazyLock::new(|| {
    TABLE
        .iter()
        .map(|(code, name, _, _)| (code.to_lowercase(), Language { code, name }))
        .collect()
});
static ALIASES: LazyLock<HashMap<String, (&'static str, &'static str)>> = LazyLock::new(|| {
    let mut map = HashMap::new();
    for (code, _, aliases, _) in TABLE {
        for alias in *aliases {
            map.insert(alias.to_string(), (*code, ""));
        }
    }
    for (alias, code, note) in GENERIC {
        map.insert(alias.to_string(), (*code, *note));
    }
    map
});
static CODES: LazyLock<HashMap<String, (&'static str, &'static str)>> = LazyLock::new(|| {
    let mut map: HashMap<String, (&'static str, &'static str)> = TABLE
        .iter()
        .map(|(code, ..)| (code.to_lowercase(), (*code, "")))
        .collect();
    for (code, _, _, extra) in TABLE {
        for e in *extra {
            map.insert(e.to_string(), (*code, ""));
        }
    }
    map.insert("pt".into(), ("pt-BR", PORTUGUESE_NOTE));
    map.insert("zh".into(), ("zh-Hans", CHINESE_NOTE));
    map
});

fn alternation<'a>(items: impl Iterator<Item = &'a String>) -> String {
    let mut sorted: Vec<&String> = items.collect();
    sorted.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()).then(a.cmp(b)));
    sorted
        .iter()
        .map(|s| regex::escape(s))
        .collect::<Vec<_>>()
        .join("|")
}

static NAME_RE: LazyLock<Regex> =
    LazyLock::new(|| re(&format!("(?i)({})", alternation(ALIASES.keys()))));
static CODE_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(&format!(
        r"(?i)\b(?:to|into|in)\s+({})\s*",
        alternation(CODES.keys())
    ))
});
static TRADITIONAL_HINT: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)\btraditional\b|\btaiwan|\bhong kong\b"));
static FROM: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bfrom\s+$"));

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Language names that stand alone (no word char right before or after), as (start, end, alias).
fn names(text: &str) -> Vec<(usize, usize, String)> {
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(m) = NAME_RE.find_at(text, pos) {
        let before = text[..m.start()].chars().next_back();
        let after = text[m.end()..].chars().next();
        if before.is_none_or(|c| !is_word(c)) && after.is_none_or(|c| !is_word(c)) {
            out.push((m.start(), m.end(), m.as_str().to_lowercase()));
            pos = m.end();
        } else {
            pos = m.start() + text[m.start()..].chars().next().map_or(1, char::len_utf8);
        }
        if pos >= text.len() {
            break;
        }
    }
    out
}

/// "to <code>" followed by punctuation or the end, as (start, code start, code end, code).
fn coded(text: &str) -> Vec<(usize, usize, usize, String)> {
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(caps) = CODE_RE.captures_at(text, pos) {
        let (Some(whole), Some(code)) = (caps.get(0), caps.get(1)) else {
            break;
        };
        let gap = &text[code.end()..whole.end()];
        let rest = &text[whole.end()..];
        let next = rest.chars().next();
        let ends = next.is_none_or(|c| ":,.;!?)\n".contains(c)) || gap.contains('\n');
        if ends {
            out.push((
                whole.start(),
                code.start(),
                whole.end(),
                code.as_str().to_lowercase(),
            ));
            pos = whole.end();
        } else {
            pos = whole.start()
                + text[whole.start()..]
                    .chars()
                    .next()
                    .map_or(1, char::len_utf8);
        }
        if pos >= text.len() {
            break;
        }
    }
    out
}

pub fn by_code(code: &str) -> Option<Language> {
    BY_CODE.get(&code.to_lowercase()).cloned()
}

/// The English name of a supported code, else the code itself.
pub fn name_of(code: &str) -> String {
    by_code(code).map_or(code.to_string(), |l| l.name.to_string())
}

fn matched(code: &str, note: &'static str, text: &str) -> Option<Match> {
    if code == "zh-Hans" && TRADITIONAL_HINT.is_match(text) {
        return by_code("zh-hant").map(|language| Match { language, note: "" });
    }
    by_code(code).map(|language| Match { language, note })
}

/// (target, source hint) named in an instruction. A name right after "from" is the source.
pub fn find(text: &str) -> (Option<Match>, Option<Match>) {
    let (mut target, mut source) = (None, None);
    for (start, _, alias) in names(text) {
        let Some((code, note)) = ALIASES.get(&alias).copied() else {
            continue;
        };
        if FROM.is_match(&text[..start]) {
            if source.is_none() {
                source = matched(code, "", text);
            }
        } else if target.is_none() {
            target = matched(code, note, text);
        }
    }
    if target.is_none()
        && let Some((_, _, _, code)) = coded(text).into_iter().next()
    {
        target = CODES.get(&code).and_then(|(c, n)| matched(c, n, text));
    }
    (target, source)
}

/// `text` without any language name, endonym or "to <code>" reference.
pub fn strip_names(text: &str) -> String {
    let mut named = String::new();
    let mut last = 0;
    for (start, end, _) in names(text) {
        named.push_str(&text[last..start]);
        named.push(' ');
        last = end;
    }
    named.push_str(&text[last..]);
    let mut out = String::new();
    let mut last = 0;
    for (_, code_start, end, _) in coded(&named) {
        out.push_str(&named[last..code_start]);
        last = end;
    }
    out.push_str(&named[last..]);
    out
}

/// A whole answer to "which language?": a name, an endonym or a code.
pub fn from_answer(answer: &str) -> Option<Match> {
    let plain = collapse_ws(strip_chars(answer.trim(), ".!")).to_lowercase();
    if let Some((code, note)) = CODES.get(&plain) {
        return matched(code, note, &plain);
    }
    find(&plain).0
}

/// A language for a code as the user reads it ("Spanish", not "Spanish (Mexico)").
pub fn name_for(code: &str) -> String {
    if code.is_empty() {
        return "an undetected language".to_string();
    }
    let base = code.split('-').next().unwrap_or("").to_lowercase();
    let lang = by_code(code).or_else(|| {
        TABLE
            .iter()
            .find(|(c, ..)| {
                c.split('-')
                    .next()
                    .unwrap_or("")
                    .eq_ignore_ascii_case(&base)
            })
            .map(|(code, name, ..)| Language { code, name })
    });
    match lang {
        Some(l) => l.name.split(" (").next().unwrap_or(l.name).to_string(),
        None => code.to_string(),
    }
}

/// The target as the model is told it; plain "es" is neutral Latin American Spanish.
pub fn prompt_name(code: &str) -> String {
    if code == "es" {
        return "Spanish (neutral Latin American)".to_string();
    }
    name_of(code)
}

pub fn base(code: &str) -> String {
    code.split('-').next().unwrap_or("").to_lowercase()
}

pub fn is_cjk(code: &str) -> bool {
    CJK.contains(&base(code).as_str())
}

pub fn supported_names() -> Vec<&'static str> {
    TABLE.iter().map(|(_, name, ..)| *name).collect()
}
