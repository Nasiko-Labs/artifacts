"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "doc-converter"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns a document into clean Markdown: PDF (text layer), Word, PowerPoint, Excel, CSV, HTML, JSON, XML, "
    "EPUB, plain text, or a ZIP of those. Name up to 5 files in your cloud drive or file storage, paste HTML "
    "or a CSV, or send a file through the API. The conversion is done in code, so the document's content "
    "never reaches a model; the result comes back in a Markdown block you can copy or download, with page, "
    "sheet and slide counts and honest warnings (scanned PDF with no text layer, cut sheets). Read-only. It "
    "does not summarise, translate, rewrite or answer questions about a document."
)

SKILL_ID = "convert-to-markdown"
SKILL_NAME = "Convert a document to Markdown"
SKILL_DESCRIPTION = (
    "A PDF, Word, PowerPoint, Excel, CSV, HTML, JSON, XML, EPUB, text or ZIP file from your cloud drive, file "
    "storage, a paste or the API, converted to Markdown in code, with counts and warnings. Say 'as json' for "
    "a workflow envelope."
)
SKILL_TAGS = ["markdown", "convert", "pdf", "docx", "xlsx", "pptx", "html", "csv", "documents"]
SKILL_EXAMPLES = [
    "Convert Q3-plan.docx from my Drive to markdown",
    "Turn the deck 'Board update.pptx' in Dropbox into markdown",
    "Convert this HTML to markdown: <paste>",
]
