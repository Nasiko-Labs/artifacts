"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "dependency-risk-scanner"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Tells you which of your repositories are exposed to a dependency advisory you paste (a CVE or GHSA id with "
    "the package, ecosystem, affected range and fixed version, or a deprecation notice). It reads the manifests "
    "and lockfiles of the repositories you name (or an owner's), and says per repository which version is "
    "declared or resolved and whether it is affected, may resolve to an affected version, is not affected, or "
    "is unknown. Version and range matching is done in code, never guessed. For one repository it can open an "
    "upgrade pull request that bumps the version in the manifest, writing each step (branch, commit, PR) only "
    "after you approve that one step. It never fetches advisories itself, runs installs, edits lockfiles or merges."
)

SKILL_ID = "dependency-exposure-scan"
SKILL_NAME = "Dependency exposure scan"
SKILL_DESCRIPTION = (
    "Which repositories declare a package named in an advisory, at which declared and resolved versions, and "
    "whether each is inside the affected range (computed in code); then an upgrade pull request for one "
    "repository, each write approved on its own card."
)
SKILL_TAGS = ["security", "dependencies", "vulnerability", "cve", "advisory", "upgrade", "pull request", "devops"]
SKILL_EXAMPLES = [
    "CVE-2026-1234 affects lodash (npm) <4.17.21, fixed in 4.17.21: which of acme/web, acme/api use it?",
    "Is acme/api exposed to this advisory? <paste the advisory>",
    "Open the upgrade PR for acme/web",
]
