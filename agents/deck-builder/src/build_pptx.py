"""The deck spec → .pptx bytes with python-pptx: one function per layout, shapes and text only (no images),
native charts and tables from the parsed data. The output is deterministic (fixed document dates and zip
timestamps), so the bytes the user approved can be rebuilt and compared by hash before an upload."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Inches, Pt

import theme as t
from charts import axis_max, chart_data
from dataparse import Table
from models import Deck, Slide

FIXED_TIME = datetime(2026, 1, 1, 0, 0, 0)
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
BLANK_LAYOUT = 6
_CHART_TYPES = {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "bar": XL_CHART_TYPE.BAR_CLUSTERED,
                "line": XL_CHART_TYPE.LINE_MARKERS, "pie": XL_CHART_TYPE.PIE}


def build(deck: Deck, tables: dict[str, Table]) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(t.SLIDE_W_IN), Inches(t.SLIDE_H_IN)
    _properties(prs, deck)
    for spec in deck.slides:
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK_LAYOUT])
        _LAYOUTS.get(spec.layout, _bullets)(slide, spec, deck, tables)
        if spec.notes:
            slide.notes_slide.notes_text_frame.text = spec.notes
    buffer = io.BytesIO()
    prs.save(buffer)
    return _normalise_zip(buffer.getvalue())


def _properties(prs, deck: Deck) -> None:
    props = prs.core_properties
    props.title = deck.title[:255]
    props.author = props.last_modified_by = "deck-builder"
    props.created = props.modified = FIXED_TIME
    props.revision = 1


def _normalise_zip(data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            fixed = zipfile.ZipInfo(info.filename, date_time=_ZIP_TIME)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(fixed, src.read(info.filename))
    return out.getvalue()


def _rgb(hex_colour: str) -> RGBColor:
    return RGBColor.from_string(hex_colour)


def _text(slide, box: t.Box, lines: list[str], pt: int, *, bold: bool = False, colour: str = t.TEXT_COLOUR,
          bullets: bool = False):
    frame = slide.shapes.add_textbox(Inches(box.left), Inches(box.top), Inches(box.width), Inches(box.height)).text_frame
    frame.word_wrap = True
    for i, line in enumerate(lines or [""]):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        run = para.add_run()
        run.text = f"• {line}" if bullets else line
        run.font.size, run.font.bold, run.font.name = Pt(max(pt, t.MIN_SLIDE_PT)), bold, t.FONT
        run.font.color.rgb = _rgb(colour)
    return frame


def _title(slide, spec: Slide, deck: Deck, _tables=None) -> None:
    _text(slide, t.TITLE_BOX, [spec.title], t.TITLE_PT, bold=True, colour=deck.theme.accent)


def _title_slide(slide, spec: Slide, deck: Deck, _tables) -> None:
    box = t.Box(t.MARGIN_IN, 2.4, t.TITLE_BOX.width, 1.6)
    _text(slide, box, [spec.title or deck.title], 40, bold=True, colour=deck.theme.accent)
    sub = [s for s in (deck.subtitle, *spec.bullets[:1]) if s]
    if sub:
        _text(slide, t.Box(t.MARGIN_IN, 4.1, t.TITLE_BOX.width, 1.0), sub[:1], 24)


def _bullets(slide, spec: Slide, deck: Deck, _tables) -> None:
    _title(slide, spec, deck)
    _text(slide, t.BODY_BOX, spec.bullets, t.BODY_PT, bullets=True)


def _two_column(slide, spec: Slide, deck: Deck, _tables) -> None:
    _title(slide, spec, deck)
    _text(slide, t.LEFT_BOX, spec.bullets, t.BODY_PT, bullets=True)
    _text(slide, t.RIGHT_BOX, spec.right, t.BODY_PT, bullets=True)


def _big_number(slide, spec: Slide, deck: Deck, _tables) -> None:
    _title(slide, spec, deck)
    stat = spec.stat
    _text(slide, t.Box(t.MARGIN_IN, 2.2, t.BODY_BOX.width, 1.6), [stat.value if stat else ""], t.BIG_NUMBER_PT,
          bold=True, colour=deck.theme.accent)
    _text(slide, t.Box(t.MARGIN_IN, 4.0, t.BODY_BOX.width, 1.2), [stat.caption if stat else ""], 24)


def _quote(slide, spec: Slide, deck: Deck, _tables) -> None:
    _title(slide, spec, deck)
    _text(slide, t.BODY_BOX, [f"“{spec.quote}”"], 28)


def _closing(slide, spec: Slide, deck: Deck, _tables) -> None:
    _text(slide, t.Box(t.MARGIN_IN, 2.6, t.TITLE_BOX.width, 1.4), [spec.title], 36, bold=True,
          colour=deck.theme.accent)
    if spec.bullets:
        _text(slide, t.Box(t.MARGIN_IN, 4.0, t.TITLE_BOX.width, 2.8), spec.bullets[:5], t.BODY_PT, bullets=True)


def _chart(slide, spec: Slide, deck: Deck, tables: dict[str, Table]) -> None:
    _title(slide, spec, deck)
    data = chart_data(spec.chart, tables[spec.chart.table])
    source = CategoryChartData()
    source.categories = list(data.categories)
    for name, values in data.series:
        source.add_series(name, list(values))
    box = t.BODY_BOX
    chart = slide.shapes.add_chart(_CHART_TYPES[data.kind], Inches(box.left), Inches(box.top), Inches(box.width),
                                   Inches(box.height), source).chart
    chart.font.size, chart.font.name = Pt(t.MIN_SLIDE_PT), t.FONT
    chart.plots[0].has_data_labels = True
    chart.has_legend = len(data.series) > 1 or data.kind == "pie"
    if chart.has_legend:
        chart.legend.position, chart.legend.include_in_layout = XL_LEGEND_POSITION.BOTTOM, False
    if data.kind != "pie":
        chart.value_axis.minimum_scale = 0
        chart.value_axis.maximum_scale = axis_max(data)
        palette = (deck.theme.accent, deck.theme.second, deck.theme.third, t.TEXT_COLOUR)
        for series, colour in zip(chart.plots[0].series, palette, strict=False):
            if data.kind == "line":
                series.format.line.color.rgb = _rgb(colour)
            else:
                series.format.fill.solid()
                series.format.fill.fore_color.rgb = _rgb(colour)


def _table(slide, spec: Slide, deck: Deck, tables: dict[str, Table]) -> None:
    _title(slide, spec, deck)
    table = tables[spec.table.table]
    columns = spec.table.columns
    idx = [table.column(c) for c in columns]
    rows = table.rows[spec.table.rows_from:spec.table.rows_to]
    box = t.BODY_BOX
    height = min(box.height, 0.5 * (len(rows) + 1))
    grid = slide.shapes.add_table(len(rows) + 1, len(columns), Inches(box.left), Inches(box.top), Inches(box.width),
                                  Inches(height)).table
    for c, name in enumerate(columns):
        _cell(grid.cell(0, c), name, bold=True)
    for r, row in enumerate(rows, start=1):
        for c, i in enumerate(idx):
            _cell(grid.cell(r, c), row[i])


def _cell(cell, text: str, bold: bool = False) -> None:
    cell.text = ""
    run = cell.text_frame.paragraphs[0].add_run()
    run.text = text
    run.font.size, run.font.bold, run.font.name = Pt(t.MIN_SLIDE_PT), bold, t.FONT


_LAYOUTS = {"title": _title_slide, "bullets": _bullets, "two_column": _two_column, "chart": _chart,
            "table": _table, "big_number": _big_number, "quote": _quote, "closing": _closing}
