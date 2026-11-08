"""Pure: is every number, date, name and quote on a slide in what the user gave? Ungrounded tokens become
visible placeholders; nothing is guessed. The corpus is the request, the notes (minus lines that read as
injected instructions), the parsed data cells and the strings computed in code (GIVEN)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

FIGURE = "[figure?]"
NAME = "[name?]"
DATE = "[date?]"
PLACEHOLDERS = (FIGURE, NAME, DATE)

NUMBER = re.compile(r"[$€£]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?%|[kKmMbBxX]\b)?")
_CAP_RUN = re.compile(r"\b[A-Z][\w&'’-]*(?:\s+[A-Z][\w&'’-]*)*")
_WORD = re.compile(r"[a-z0-9&'’-]+")
_SPACES = re.compile(r"\s+")
_MONTHS = frozenset({"january", "february", "march", "april", "may", "june", "july", "august", "september",
                     "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
                     "sept", "oct", "nov", "dec", "monday", "tuesday", "wednesday", "thursday", "friday",
                     "saturday", "sunday"})
# Title-case words that are slide vocabulary, not names.
COMMON = frozenset(["a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on", "at", "by", "with", "from", "our", "we", "us", "you", "your", "i", "it", "its", "this", "that", "these", "those", "why", "what", "how", "where", "when", "who", "which", "next", "steps", "step", "agenda", "thank", "thanks", "questions", "q&a", "summary", "overview", "key", "takeaways", "takeaway", "decisions", "decision", "risks", "risk", "appendix", "goals", "goal", "plan", "plans", "update", "updates", "results", "highlights", "today", "now", "new", "our", "team", "teams", "customer", "customers", "board", "pitch", "status", "progress", "priorities", "priority", "ask", "asks", "recommendation", "recommendations", "conclusion", "closing", "introduction", "background", "context", "problem", "solution", "approach", "timeline", "milestones", "roadmap", "budget", "costs", "cost", "revenue", "growth", "metrics", "metric", "kpis", "kpi", "wins", "win", "challenges", "lessons", "learned", "impact", "opportunity", "opportunities", "overview", "proposal", "options", "option", "needed", "need", "what's", "we're", "let's", "is", "are", "was", "were", "be", "have", "has", "will", "can", "should", "must", "not", "no", "yes", "all", "more", "less", "up", "down"])


@dataclass(frozen=True)
class Corpus:
    text: str                    # normalised: casefolded, digit commas removed, whitespace collapsed
    words: frozenset[str]

    def has_number(self, token: str) -> bool:
        wanted = _norm_number(token)
        return bool(wanted) and re.search(rf"(?<![\d.]){re.escape(wanted)}(?![\d])", self.text) is not None

    def has_word(self, word: str) -> bool:
        return word.casefold() in self.words

    def has_fragment(self, fragment: str) -> bool:
        wanted = canonical(fragment)
        return len(wanted) >= 3 and wanted in self.text


def normalise(text: str) -> str:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text.casefold())
    return _SPACES.sub(" ", text.replace("’", "'")).strip()


def canonical(text: str) -> str:
    """normalise() without currency symbols and with "12 %" as "12%": how the corpus is stored."""
    return normalise(text).replace("$", "").replace("€", "").replace("£", "").replace(" %", "%")


def _norm_number(token: str) -> str:
    return normalise(token).replace("$", "").replace("€", "").replace("£", "").replace(" ", "").rstrip(".")


def build_corpus(parts: Iterable[str]) -> Corpus:
    text = canonical("\n".join(parts))
    return Corpus(text=text, words=frozenset(_WORD.findall(text)))


def ground_text(text: str, corpus: Corpus) -> tuple[str, int]:
    """The text with each ungrounded number, date and name replaced by a placeholder, and how many."""
    count = 0

    def number(match: re.Match) -> str:
        nonlocal count
        if corpus.has_number(match.group(0)):
            return match.group(0)
        count += 1
        return FIGURE

    out = NUMBER.sub(number, _protect(text))

    def caps(match: re.Match) -> str:
        nonlocal count
        replaced = _cap_run(match, corpus)
        count += count_placeholders(replaced)
        return replaced

    if not _title_case(text):
        out = _CAP_RUN.sub(caps, out)
    return _restore(out), count


def _title_case(text: str) -> bool:
    """"Pipeline Coverage Is Strong": capitals carry no signal, so the name pass is skipped."""
    long_words = [w for w in text.split() if len(w) > 3 and w[0].isalpha()]
    return len(long_words) >= 3 and all(w[0].isupper() for w in long_words)


def _cap_run(match: re.Match, corpus: Corpus) -> str:
    words = match.group(0).split()
    start = match.start() == 0 or match.string[:match.start()].rstrip().endswith((".", ":", "!", "?", "—", "-"))
    checked = words[1:] if start and words[0].casefold() in COMMON or start and len(words) == 1 else words
    skip = len(words) - len(checked)
    out: list[str] = list(words[:skip])
    for word in checked:
        if not _ungrounded(word, corpus):
            out.append(word)
            continue
        mark = DATE if word.casefold().strip("'’") in _MONTHS else NAME
        if not out or out[-1] != mark:  # "Acme Corp" → one placeholder
            out.append(mark)
    return " ".join(out)


def _ungrounded(word: str, corpus: Corpus) -> bool:
    plain = word.casefold().strip("'’-")
    if not plain or plain in COMMON or corpus.has_word(plain):
        return False
    return not (plain.endswith("s") and corpus.has_word(plain[:-1]))


# Placeholders already in the text are protected from the name/number passes.
def _protect(text: str) -> str:
    return text.replace(FIGURE, "\x00f").replace(NAME, "\x00n").replace(DATE, "\x00d")


def _restore(text: str) -> str:
    return text.replace("\x00f", FIGURE).replace("\x00n", NAME).replace("\x00d", DATE)


def count_placeholders(text: str) -> int:
    return sum(text.count(p) for p in PLACEHOLDERS)
