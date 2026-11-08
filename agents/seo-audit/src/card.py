"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "seo-audit"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Audits one web page for on-page SEO and for readiness to be cited by AI answer engines. Paste the "
    "page's HTML (or give one URL, fetched through a strict guard that only reaches public addresses) and "
    "it returns a score computed in code, findings with severity and evidence quoted from the page, a "
    "prioritised fix list, and proposed title and meta description rewrites with their lengths measured in "
    "code. Follow-ups work on the stored audit: only critical, explain a finding, score breakdown, AI "
    "crawler rules, rewrite the title for a keyword, audit again with a new version. It never crawls a "
    "site, writes nothing, and does not report rankings, backlinks, traffic or page speed."
)

SKILL_ID = "seo-audit-page"
SKILL_NAME = "Audit a page for SEO and AI search"
SKILL_DESCRIPTION = (
    "A code-computed SEO and AI-readiness score for one page (pasted HTML or one URL), findings with "
    "verbatim evidence, a fix list and title/meta rewrite proposals, plus follow-ups on the same audit."
)
SKILL_TAGS = ["seo", "audit", "on-page", "meta description", "title tag", "structured data", "ai search", "geo"]
SKILL_EXAMPLES = [
    "Audit https://example.com/pricing",
    "Here's my page's HTML, audit it for the keyword 'crm for startups': <paste>",
    "Is this page ready for AI search engines? <URL>",
]
