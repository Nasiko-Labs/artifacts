"""The chat fallback: a self-contained python script (python-pptx only) that rebuilds the deck from the
slide JSON embedded in it, and that JSON on its own. Chat cannot download a file part, but a code block has
Copy/Download. Chart and table data are resolved from the parsed tables here, so the script needs nothing else."""

from __future__ import annotations

import json

from charts import chart_data
from dataparse import Table
from models import Deck

_SCRIPT = '''# Rebuilds "{title}" as {file_name}. Run: pip install python-pptx && python build_deck.py
import json
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

DECK = json.loads(r"""{spec}""")
TYPES = {{"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "bar": XL_CHART_TYPE.BAR_CLUSTERED,
         "line": XL_CHART_TYPE.LINE_MARKERS, "pie": XL_CHART_TYPE.PIE}}


def text(slide, left, top, width, height, lines, size, bold=False):
    frame = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height)).text_frame
    frame.word_wrap = True
    for i, line in enumerate(lines or [""]):
        run = (frame.paragraphs[0] if i == 0 else frame.add_paragraph()).add_run()
        run.text, run.font.size, run.font.bold = line, Pt(size), bold


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
for spec in DECK["slides"]:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    text(slide, 0.6, 0.4, 12.1, 1.2, [spec["title"]], 32, bold=True)
    body = ["- " + b for b in spec.get("bullets", []) + spec.get("right", [])]
    if spec.get("stat"):
        body = [spec["stat"]["value"], spec["stat"]["caption"]]
    if spec.get("quote"):
        body = [spec["quote"]]
    chart, table = spec.get("chart"), spec.get("table")
    if chart:
        data = CategoryChartData()
        data.categories = chart["categories"]
        for name, values in chart["series"]:
            data.add_series(name, values)
        slide.shapes.add_chart(TYPES[chart["kind"]], Inches(0.6), Inches(1.8), Inches(12.1), Inches(5.2), data)
    elif table:
        rows = [table["columns"]] + table["rows"]
        grid = slide.shapes.add_table(len(rows), len(rows[0]), Inches(0.6), Inches(1.8), Inches(12.1),
                                      Inches(0.5 * len(rows))).table
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                grid.cell(r, c).text = str(value)
    elif body:
        text(slide, 0.6, 1.8, 12.1, 5.2, body, 20)
    if spec.get("notes"):
        slide.notes_slide.notes_text_frame.text = spec["notes"]
prs.save("{file_name}")
print("saved {file_name}")
'''


def resolved_spec(deck: Deck, tables: dict[str, Table]) -> dict:
    """The deck with chart and table data read from the parsed tables (what the script and JSON block hold)."""
    slides = []
    for s in deck.slides:
        spec: dict = {"layout": s.layout, "title": s.title, "bullets": s.bullets, "right": s.right, "notes": s.notes}
        if s.layout == "chart" and s.chart:
            spec["chart"] = chart_data(s.chart, tables[s.chart.table]).to_dict()
        if s.layout == "table" and s.table:
            table = tables[s.table.table]
            idx = [table.column(c) for c in s.table.columns]
            rows = table.rows[s.table.rows_from:s.table.rows_to]
            spec["table"] = {"columns": list(s.table.columns), "rows": [[r[i] for i in idx] for r in rows]}
        if s.layout == "big_number" and s.stat:
            spec["stat"] = {"value": s.stat.value, "caption": s.stat.caption}
        if s.layout == "quote":
            spec["quote"] = s.quote
        slides.append(spec)
    return {"title": deck.title, "subtitle": deck.subtitle, "slides": slides}


def script(deck: Deck, tables: dict[str, Table], file_name: str) -> str:
    spec = json.dumps(resolved_spec(deck, tables), ensure_ascii=True, indent=1).replace('"""', '\\u0022""')
    safe_name = file_name.replace('"', "").replace("\\", "")
    return _SCRIPT.format(title=deck.title.replace("\n", " ").replace('"', "'"), file_name=safe_name, spec=spec)
