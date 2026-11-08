"""Pure: the checked chunk results assembled into the output envelope (aligned items, the restored
translation, notes, confidence, needs_human), and an input envelope rebuilt with its fields translated."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import languages
import mask
import register as reg
import segments as segs
import verify
from request import Job
from translate import ChunkResult, Prepared

_LEGAL_MEDICAL = ("hereby", "liability", "indemnif", "dosage", "contraindicat", "diagnos", "patient",
                  "governing law", "warrant")
LEGAL_MIN_HITS = 2
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Result:
    status: str                     # ok | already | failed
    envelope: dict
    translated_input: dict | None = None


def build(job: Job, prepared: Prepared, chunks: list[list[int]], results: list[ChunkResult]) -> Result:
    ok = [r for r in results if r.status == "ok"]
    if not ok:
        return Result("failed", {})
    detected = Counter(r.source_language.split("-")[0].casefold() for r in ok if r.source_language)
    source_code = detected.most_common(1)[0][0] if detected else ""
    if len(ok) == len(results) and source_code == job.target.split("-")[0].casefold():
        return Result("already", {"source_language": source_code, "target_language": job.target})
    texts = [s.text for s in prepared.segments]
    for ids, result in zip(chunks, results, strict=True):
        if result.status == "ok":
            for i, text in zip(ids, result.translations, strict=True):
                texts[i] = text
    restored_src = [mask.restore(s.text, prepared.spans) for s in prepared.segments]
    restored_out = [mask.restore(t, prepared.spans) for t in texts]
    source_all = "\n\n".join(text for _, text in prepared.docs)
    notes, level = _notes(job, results, source_code, source_all, restored_src, restored_out)
    confidence = ("low" if level >= 2 else "medium" if level == 1 else "high")
    per_field = _by_field(prepared, restored_out)
    translated_input = ({**job.envelope, **per_field} if job.envelope else None)
    translation = "\n\n".join(per_field.values()) if job.envelope else per_field[None]
    envelope = {
        "summary": _summary(job, source_code, len(prepared.segments)),
        "items": [{"n": i + 1, "source": s, "translation": t} for i, (s, t) in enumerate(zip(restored_src, restored_out,
                                                                                               strict=True))],
        "translation": translation, "source_language": source_code, "target_language": job.target,
        "register": job.register, "notes": notes, "confidence": confidence,
        "needs_human": confidence == "low" or legal_or_medical(source_all),
    }
    return Result("ok", envelope, translated_input)


def _by_field(prepared: Prepared, restored_out: list[str]) -> dict:
    fields: dict = {}
    for name, _ in prepared.docs:
        ids = [i for i, s in enumerate(prepared.segments) if s.field == name]
        fields[name] = segs.join([restored_out[i] for i in ids], [prepared.segments[i] for i in ids])
    return fields


def _notes(job: Job, results: list[ChunkResult], source_code: str, source_all: str, restored_src: list[str],
           restored_out: list[str]) -> tuple[list[dict], int]:
    """(notes, level) where level 0 = high, 1 = medium, 2 = low confidence."""
    level = 0
    code: list[str] = []
    if job.target_note:
        code.append(job.target_note)
        level = 1
    if (register_note := reg.note(job.target.split("-")[0].casefold(), job.register)):
        code.append(register_note)
    if any(r.repaired or r.structure_fixed or r.code_notes for r in results if r.status == "ok"):
        level = max(level, 1)
    for r in results:
        code += r.code_notes
    if any(r.digits_left for r in results if r.status == "ok"):
        level = 2
    missing = [n for n, r in enumerate(results, start=1) if r.status != "ok"]
    if missing:
        level = 2
        skipped = sum(r.status == "skipped" for r in results)
        done = len(results) - len(missing)
        code.append(f"Translated the first {done} of {len(results)} parts before my time limit; the rest is left "
                    "in the original." if skipped else
                    f"Part{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} could not be translated "
                    "and are left in the original.")
    length = verify.length_note("\n".join(restored_src), "\n".join(restored_out), source_code, job.target)
    if length:
        code.append(length)
        level = max(level, 1)
    joined_out = "\n".join(restored_out).casefold()
    for term, wanted in job.glossary:
        if term.casefold() in source_all.casefold() and wanted.casefold() not in joined_out:
            code.append(f"Glossary: \"{term}\" should be \"{wanted}\", which is not in the translation; check it.")
            level = max(level, 1)
    model = verify.model_notes([n for r in results for n in r.notes], source_all)
    return [{"term": "", "note": n} for n in code] + model, level


def legal_or_medical(text: str) -> bool:
    lowered = _SPACE.sub(" ", (text or "").casefold())
    return sum(word in lowered for word in _LEGAL_MEDICAL) >= LEGAL_MIN_HITS


def _summary(job: Job, source_code: str, paragraphs: int) -> str:
    unit = "segment" if paragraphs == 1 else "segments"
    target = languages.BY_CODE[job.target.casefold()].name
    return (f"Translated {paragraphs} {unit} from {languages.name_for(source_code)} to {target} "
            f"({job.register}).")
