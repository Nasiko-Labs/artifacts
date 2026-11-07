"""Pure: the evidence list: code sentences with exact numbers, numbered e1.. (at most 25). The model may
only cite these; every number it writes must appear in the evidence it cites."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import analysis as an
from decompose import Breakdown, Seg
from fmt import fmt, pct, pp, rate, signed

MAX_EVIDENCE = 25
TOP_SEGMENTS = 3
CROSS_EVIDENCE = 3


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str              # total | concentration | split | simpson | segment | new | offset | cross | quality
    text: str
    dimension: str = ""
    segment: str = ""
    effect: float = 0.0
    share_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def build(result: an.Analysis) -> tuple[Evidence, ...]:
    drafts: list[tuple] = [("total", _total(result), "", "", 0.0, None)]
    drafts += _quality(result)
    for b in result.dims:
        drafts += _dimension(result, b)
    if result.cross is not None:
        drafts += [("cross", seg_sentence(result.cross, s), result.cross.dim, s.name, s.effect, s.share)
                   for s in result.cross.segs[:CROSS_EVIDENCE]]
    return tuple(Evidence(f"e{n}", *d) for n, d in enumerate(drafts[:MAX_EVIDENCE], start=1))


def _total(result: an.Analysis) -> str:
    b = result.top
    if result.kind == "rate":
        test = result.total_test
        return (f"Overall {result.metric}: {rate(b.before)} → {rate(b.after)} ({pp(b.delta)}) across "
                f"{fmt(result.segments)} segments; {test.label} (z = {test.z:.2f}).")
    change = f", {b.delta / b.before * 100:+.1f}%" if b.before else ""
    return (f"Total {result.metric}: {fmt(b.before)} → {fmt(b.after)} ({signed(b.delta)}{change}) across "
            f"{fmt(result.segments)} segments.")


def _quality(result: an.Analysis) -> list[tuple]:
    out = []
    if an.is_mismatch(result):
        text = f"The segments do not add up to the table's Total row: they differ by {result.mismatch_pct:.1f}%."
        out.append(("quality", text, "", "", 0.0, None))
    for b, s in result.small_movers():
        text = (f"{b.dim} = {s.name} has only {fmt(min(s.n0, s.n1))} in its smaller period (below the minimum "
                f"sample of {fmt(result.min_n)}).")
        out.append(("quality", text, b.dim, s.name, s.effect, s.share))
    return out


def _dimension(result: an.Analysis, b: Breakdown) -> list[tuple]:
    out = [("concentration", _concentration(b), b.dim, "", 0.0, None)]
    if b.kind == "rate":
        split = (f"By {b.dim}: mix effect {pp(b.total_mix)} ({b.mix_share * 100:.0f}%), rate effect "
                 f"{pp(b.total_rate)} ({b.rate_share * 100:.0f}%): {b.driver}.")
        out.append(("split", split, b.dim, "", 0.0, None))
        if b.simpson:
            simpson = (f"By {b.dim}: the overall rate moved opposite to segments holding "
                       f"{b.simpson_share * 100:.0f}% of after-period volume: a mix shift.")
            out.append(("simpson", simpson, b.dim, "", b.total_mix, None))
    top = b.segs[:TOP_SEGMENTS]
    out += [("segment", seg_sentence(b, s), b.dim, s.name, s.effect, s.share) for s in top]
    extra = [s for s in b.segs[TOP_SEGMENTS:] if s.new or s.vanished][:2]
    out += [("new", seg_sentence(b, s), b.dim, s.name, s.effect, s.share) for s in extra]
    if b.conc.offsets:
        names = ", ".join(b.segs[i].name for i in b.conc.offsets[:4])
        offset = (f"By {b.dim}: {names} moved against the overall change, offsetting "
                  f"{b.conc.offset_share * 100:.0f}% of it.")
        out.append(("offset", offset, b.dim, "", 0.0, None))
    return out


def _concentration(b: Breakdown) -> str:
    c = b.conc
    near = (f" The net change is near zero, so shares are n/a; gross movement {_amount(b, c.gross)}."
            if b.near_zero else "")
    return (f"By {b.dim}: the top segment holds {pct(c.top1 * 100)} of the gross movement and the top 3 hold "
            f"{pct(c.top3 * 100)} ({c.label}).{near}")


def _amount(b: Breakdown, value: float) -> str:
    return pp(value) if b.kind == "rate" else fmt(value)


def seg_sentence(b: Breakdown, s: Seg) -> str:
    tag = " (new segment)" if s.new else " (vanished segment)" if s.vanished else ""
    share = f"{pct(s.share)} of the net change" if s.share is not None else "share n/a, net change near zero"
    if b.kind == "additive":
        return f"{b.dim} = {s.name}{tag}: {fmt(s.b)} → {fmt(s.a)} ({signed(s.effect)}), {share}."
    test = s.test
    sig = f"{test.label} (z = {test.z:.2f})" if test and test.label != "not testable" else "not testable"
    return (f"{b.dim} = {s.name}{tag}: rate {rate(s.b)} → {rate(s.a)} ({pp(s.a - s.b)}); share of volume "
            f"{pct(s.w0 * 100)} → {pct(s.w1 * 100)}; mix effect {pp(s.mix)}, rate effect {pp(s.rate)}; {share}; "
            f"{sig}.")
