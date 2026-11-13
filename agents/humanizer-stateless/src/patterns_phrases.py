"""The AI-writing pattern lexicon: paraphrased categories (adapted from the open-source "signs of AI writing"
catalogue; no text copied verbatim), each a list of case-insensitive regexes. Pure data."""

from __future__ import annotations

# Counted categories, in priority order: when two matches overlap, the longer wins, then the earlier here.
CATEGORIES = (
    "chatbot", "sycophancy", "significance", "promotional", "filler", "formulaic", "vague_attribution",
    "negative_parallelism", "ing_analysis", "copula", "upbeat_conclusion", "vocabulary", "rule_of_three",
    "boldface_headers", "typography", "em_dash",
)
# Judged by the model only; never counted in the density.
MODEL_ONLY = ("synonym_cycling", "false_range")
CHANGE_CATEGORIES = CATEGORIES + MODEL_ONLY
ARTEFACT_CATEGORIES = ("chatbot", "sycophancy")

LABELS = {
    "chatbot": "chatbot opener/closer", "sycophancy": "sycophancy", "significance": "significance inflation",
    "promotional": "promotional", "filler": "filler", "formulaic": "formulaic challenges-and-future",
    "vague_attribution": "vague attribution", "negative_parallelism": "negative parallelism",
    "ing_analysis": "trailing -ing clause", "copula": "copula avoidance", "upbeat_conclusion": "upbeat conclusion",
    "vocabulary": "vocabulary", "rule_of_three": "rule of three", "boldface_headers": "bold inline headers",
    "typography": "typography", "em_dash": "em dash", "synonym_cycling": "synonym cycling",
    "false_range": "false range",
}

_S = r"(?:'|’)"  # straight or curly apostrophe

PHRASES: dict[str, tuple[str, ...]] = {
    "chatbot": (
        r"\bI hope this helps\b", r"\b(?:certainly|absolutely)!", r"\bgreat question\b",
        rf"\blet me know if you{_S}?d like\b", r"\blet me know if you would like\b", r"\bfeel free to reach out\b",
        r"\bas an AI\b", r"\bas of my last (?:update|knowledge update|training)\b", rf"\bI{_S}d be happy to\b",
        r"\bI would be happy to\b", rf"^\s*sure,? here{_S}?s\b",
    ),
    "sycophancy": (
        r"\bI (?:completely |totally )?understand your frustration\b", r"\bthank you for your valuable feedback\b",
        rf"\byou{_S}re absolutely right\b", r"\byou are absolutely right\b", r"\bwhat a great (?:question|idea)\b",
    ),
    "significance": (
        r"\bserves? as a testament\b", r"\bstands? as a (?:testament|symbol|beacon|reminder)\b", r"\ba testament to\b",
        r"\bplays? an? (?:vital|pivotal|crucial|key|significant) role\b",
        r"\bmarks? a (?:pivotal|significant) (?:moment|milestone|shift|step)\b",
        r"\bunderscores? the importance\b", r"\ba rich tapestry\b", r"\b(?:ever-)?evolving landscape\b",
        r"\bbroader landscape\b", r"\bleaves? a lasting (?:impact|legacy|impression)\b", r"\bsetting the stage for\b",
    ),
    "promotional": (
        r"\brenowned\b", r"\bnestled\b", r"\bboasts?\b", r"\bbreathtaking\b", r"\bgroundbreaking\b",
        r"\bmust-see\b", r"\brich cultural heritage\b", r"\bcommitment to excellence\b", r"\bcutting-edge\b",
        r"\bseamless(?:ly)?\b", r"\bunprecedented\b", r"\bgame[- ]chang(?:er|ing)\b", r"\bbest-in-class\b",
        r"\bstate-of-the-art\b",
        (r"\bunlock(?:s|ed|ing)? (?:(?:new|unprecedented|untapped|real|the full|its full|their full|your full) )*"
         r"(?:value|potential|possibilities|opportunities|growth)\b"),
    ),
    "filler": (
        r"\bit is important to note that\b", rf"\bit{_S}s important to note that\b",
        rf"\bit{_S}s worth noting(?: that)?\b", r"\bit is worth noting(?: that)?\b", r"\bin order to\b",
        r"\bdue to the fact that\b", r"\bat this point in time\b", r"\bha(?:s|ve) the ability to\b",
        (rf"\bin today{_S}s (?:(?:fast-paced|ever-changing|rapidly evolving|digital|modern|competitive|dynamic),? )+"
         r"(?:world|environment|landscape|age|era|economy)\b"),
        rf"\bit(?: is|{_S}s) (?:crucial|essential|vital|important|imperative) (?:to|that)\b", r"\bin conclusion\b",
        r"(?:^|(?<=[.!?]\s))overall,", r"\b(?:may|could) potentially\b", r"\bmight possibly\b",
        r"\bit goes without saying\b", r"\bat the end of the day\b",
    ),
    "formulaic": (
        r"\bdespite these challenges\b", r"\bfuture prospects\b", r"\blooking ahead\b",
        r"\bchallenges and opportunities\b",
    ),
    "vague_attribution": (
        r"\bexperts (?:say|believe|argue|agree|suggest)\b", r"\bindustry reports\b", r"\bobservers (?:note|say)\b",
        r"\bstudies (?:show|suggest|have shown)\b", r"\bsome critics (?:argue|say)\b",
        r"\bit is widely (?:believed|acknowledged|recognized|recognised)\b",
    ),
    "negative_parallelism": (
        r"\bnot only\b[^.!?\n]{0,120}?\bbut(?: also)?\b", rf"\bit{_S}?s not just\b[^.!?\n]{{0,80}}?,\s*it{_S}?s\b",
        r"\bnot merely\b", r"\bno longer just\b",
    ),
    "ing_analysis": (
        (r",\s(?:highlighting|underscoring|showcasing|reflecting|fostering|ensuring|contributing to|emphasi[sz]ing"
         r"|demonstrating|signal(?:l)?ing)\b[^.!?\n]{0,120}"),
    ),
    "copula": (r"\bserves as\b", r"\bstands as\b", r"\bfunctions as\b", r"\brepresents a\b"),
    "vocabulary": (
        r"\bdelv(?:e|es|ed|ing)\b", r"\btapestr(?:y|ies)\b", r"\btestament\b", r"\bintricate\b", r"\binterplay\b",
        r"\bpivotal\b", r"\bcrucial\b", r"\bgarner(?:s|ed|ing)?\b", r"\bfoster(?:s|ed|ing)?\b",
        r"\bshowcas(?:e|es|ed|ing)\b", r"\bunderscor(?:e|es|ed|ing)\b", r"\bvibrant\b", r"\benhanc(?:e|es|ed|ing)\b",
        r"(?:^|(?<=[.!?]\s))additionally\b", r"\baligns? with\b", r"\bvaluable insights?\b",
        r"\bnavigat(?:e|ing) the complexities\b", r"\blandscape\b", r"\bleverag(?:e|es|ed|ing)\b",
        r"\bsynerg(?:y|ies|istic)\b", r"(?:^|(?<=[.!?]\s))(?:moreover|furthermore)\b", r"\brobust\b",
        r"\bempower(?:s|ed|ing|ment)?\b", r"\bstakeholders?\b", r"\bholistic\b", r"\bparadigm\b",
        r"\bstreamlin(?:e|es|ed|ing)\b",
    ),
    "boldface_headers": (r"^[ \t]*(?:[-*•][ \t]+)?\*\*[^*\n]{1,40}?:\*\*", r"\*\*[^*\n]{1,40}\*\*:"),
}

# Only checked against the draft's last sentence.
UPBEAT_ENDINGS = (
    r"\bthe future looks bright\b", r"\bexciting times (?:are )?ahead\b", r"\bpoised for (?:success|growth)\b",
    r"\bthe possibilities are endless\b",
)

# "A, B, and C" counts as a padded triple only when all three come from this list.
POWER_WORDS = frozenset({
    "innovative", "dynamic", "impactful", "robust", "scalable", "seamless", "efficient", "effective", "reliable",
    "powerful", "flexible", "intuitive", "comprehensive", "strategic", "transformative", "engaging", "inclusive",
    "sustainable", "agile", "resilient", "holistic", "cutting-edge", "streamlined", "meaningful", "valuable",
    "exceptional", "outstanding", "remarkable", "vibrant", "diverse", "collaborative", "creative", "proactive",
    "secure", "modern", "unique", "insightful", "thoughtful", "actionable", "world-class",
})

EM_DASH = r"—|(?<=\s)--(?=\s)|(?<=\s)–(?=\s)"
EM_DASH_WORDS_PER_DASH = 150
