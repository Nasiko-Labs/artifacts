# artifacts

Deployable sources for the Nasiko artifact registry (registry.nasiko.dev).

| Folder | Registry type |
| --- | --- |
| agents/ | agent (one folder per agent: AgentCard.json, Dockerfile, requirements.txt, src/) |
| mcps/ | mcp |
| skills/ | skill |
| tools/ | tool |
| executables/ | executable |

Each agent folder is the exact contents of its deployable zip (latest version), so the
registry tarball holds no tests or caches.

```sh
cd agents/<name> && nasiko-ee registry publish .        # owner: nasiko
nasiko import nasiko/<name>:<version>                     # deploy to a control plane
```

## Agents (80)

| Name | Version | What it does |
| --- | --- | --- |
| account-news | 1.0.1 | Reports what changed at one company in the last seven days that a renewal owner should know: product announcem |
| alert-noise-classifier | 1.0.0 | Classifies a list of fired alerts you paste into the chat (an incident-tool CSV export, Alertmanager or Grafan |
| alert-rule-reviewer | 1.0.0 | Reviews alert configuration you paste into the chat (Prometheus rule files, Alertmanager config, Datadog monit |
| alert-tuner | 1.0.0 | Finds noisy alerts from your incident pager's history and proposes how to quiet them |
| billing-export-analyzer | 1.0.0 | Analyzes a cloud billing / cost export you paste into the chat (CSV with a header row: date, service, cost, pl |
| blast-radius-reviewer | 1.0.0 | Reviews a code diff you paste (git diff, PR  |
| board-metrics-updater | 1.0.0 | Computes your company's subscription revenue metrics for a period (default: last full calendar month; also a q |
| bug-to-customer-notifier | 1.0.0 | When a bug in your tracker is Done or Released, confirms the fix actually shipped (a published, non-draft rele |
| category-briefing | 1.0.3 | A CrewAI crew (researcher, then editor) that reads the market this week: what enterprise buyers are reacting t |
| churn-early-warning | 1.0.0 | Ranks which paying customers might churn, on request: it reads your payments data for cancellations, scheduled |
| ci-failure-detective | 1.0.0 | Tells you why a CI run failed |
| code-reviewer | 1.0.0 | Reviews a code diff you paste (unified diff, e |
| commitment-audit | 1.0.0 | Audits a team's committed work for a quarter |
| comms-followup | 0.3.0 | Writes the next message on an existing thread (email or Slack), such as a renewal follow-up |
| config-explainer | 1.0.0 | Explains a pasted regular expression, cron schedule, AWS IAM policy or nginx configuration in plain English an |
| context-aware-pr-reviewer | 1.0.0 | Reviews one pull request against what it was supposed to do, not just the diff |
| contract-review | 1.1.0 | Reviews an MSA against the company playbook: one pass or break per playbook rule, quoting the contract clause  |
| csv-to-insight | 1.0.0 | Turns a CSV table you paste into the chat (sales, signups, tickets, any table with a header row) into key find |
| customer-call-miner | 1.0.0 | Mines this week's recorded customer calls: reads each external call's transcript, matches the call to its CRM  |
| day-planner | 1.0.0 | Turns your own calendar into a day plan: today (or a day you name, up to 5 days) as an ordered agenda with eac |
| decision-log-builder | 1.0.0 | Scans your chat channels over the last 7 days (1 to 14 if you ask, or since Monday for 'this week') for decisi |
| decision-memo-writer | 1.0.0 | Writes a one-page decision memo from the decision, options, constraints, criteria and data you PASTE: context, |
| dependency-risk-scanner | 1.0.0 | Tells you which of your repositories are exposed to a dependency advisory you paste (a CVE or GHSA id with the |
| design-doc-drafter | 1.0.0 | Turns requirements you paste into the chat into a software design document with a fixed structure: context and |
| drafter | 1.0.0 | Writes one message from you to a specific person or group, in your tone and in the right register for where yo |
| escalation-summarizer | 1.0.0 | Turns a messy escalation thread you paste (ticket comments, a chat or email chain, internal notes) into what t |
| exec-brief-writer | 1.0.0 | Turns the outputs of other agents (or your own notes) that you PASTE into one conversation into a one-page exe |
| failed-payment-rescuer | 1.0.0 | Finds failed payments in your own payments account over the last 7 days (1 to 30 if you ask, or since your las |
| feedback-clusterer | 1.0.0 | Groups customer feedback you paste into the chat (support tickets, survey answers, app reviews, call notes; a  |
| feedback-to-roadmap | 1.0.0 | Pulls recent customer feedback from your support desk and a feedback channel in chat (plus anything you paste) |
| flaky-test-hunter | 1.0.0 | Finds the flaky tests in a repository's recent CI history |
| iac-cost-gate | 1.0.0 | Estimates the monthly AWS cost of Terraform HCL, a terraform show -json plan or a CloudFormation template you  |
| inbox-to-linear | 1.0.0 | Scans your recent inbox (last 3 days by default, 1 to 14 if you ask) for emails that are really work requests: |
| inbox-triage | 1.0.0 | Reads your recent inbox (last 2 days by default, 1 to 14 if you ask) and sorts each thread into urgent (needs  |
| incident-copilot | 1.0.0 | On-call incident copilot |
| invoice-chaser | 1.0.0 | Lists your open customer invoices that are past their due date from your payments account, checks your own sen |
| macro-gap-finder | 1.0.0 | Finds the recurring manual answers in support replies you paste (CSV, JSON or plain text, 10 to 2,000 replies) |
| meeting-prep | 2.0.0 | Two jobs |
| metric-mover-offline | 1.0.0 | Explains why a metric moved from a before/after table you paste into the chat, broken down by segment (channel |
| onboarding-guide-generator | 1.0.0 | Builds a repository walkthrough for a new hire and their first ticket, from what the repository actually conta |
| one-on-one-prep | 1.0.0 | Prepares a manager's next 1:1 with one report from the last meeting's notes and the recent updates the manager |
| performance-review-drafter | 1.0.0 | Drafts a performance review for one person from the examples and feedback a manager pastes into the chat |
| postmortem-follow-through | 1.0.0 | Finds the postmortems from the last 6 months (up to 12 if you ask) in your tracker, and in your postmortem doc |
| postmortem-writer | 1.0.0 | Turns an incident timeline or chat log you paste (a chat thread, pager notes, a bullet timeline) into a blamel |
| pr-agent | 1.0.1 | Reviews GitHub pull requests, like PR-Agent: give it a PR link or owner/repo#123 and a command |
| pr-description-writer | 1.0.0 | Turns a diff you paste into the chat (git diff,  |
| pr-review-queue | 1.0.0 | Shows the pull request review queue for the repositories you name (owner/repo, up to 5): which open pull reque |
| prd-critic | 1.0.0 | Reviews a PRD you paste into the chat and returns its gaps: missing sections (problem, users, goals, non-goals |
| prd-writer | 1.0.0 | Turns a product brief you paste into the chat (the idea, who it is for, constraints, any data) into a PRD with |
| pre-mortem | 1.0.0 | Runs a pre-mortem on a plan, PRD or brief you paste into the chat: it assumes the plan already failed and writ |
| prioritizer | 1.0.0 | Ranks a task list you paste into the chat (free text, bullets, or another agent's action items), with optional |
| promise-extractor | 1.0.0 | Finds the commitments you made in text you paste (your sent emails, a chat export, meeting notes): "I'll send  |
| promise-tracker | 1.0.3 | Finds what YOU committed to in your own sent email and chat over the last 7 days (up to 14 if you ask): "I'll  |
| query-explainer | 1.0.0 | Explains SQL you paste into the chat: what the query computes, step by step in plain English, the assumptions  |
| question-deduplicator | 1.0.0 | Before you ask a question in team chat, checks whether it was already asked and answered there |
| release-notes-writer | 1.0.0 | Turns the merged PR titles, commits or shipped issues you paste into customer-facing release notes grouped by  |
| reply-drafter | 1.0.0 | Drafts your reply to a message you received, in your own voice |
| research | 1.1.0 | Reports a company's public GitHub footprint: its top public repositories, the languages they use, and which re |
| research-synthesizer | 1.0.0 | Synthesizes a pile of sources you paste into the chat (interview notes, articles, reports, forum threads, call |
| security-reviewer | 1.0.0 | Reviews code or a diff you paste into the chat for security problems: injection, authentication and authorizat |
| shift-handoff-writer | 1.0.0 | Turns the day's notes you paste into the chat (queue notes, ticket updates, chat jottings, a running log) into |
| slack-catch-up | 1.0.0 | Catches you up on your team chat workspace: for the channels and direct messages you are in, since your last c |
| slack-thread-to-issue | 1.0.0 | Scans your chat channels (the ones you name, else your 8 most active; last 3 days by default, 1 to 14 if you a |
| spec-to-issues-breaker | 1.0.0 | Reads a product spec or PRD (a document found by title or link, or text you paste after "Spec:") and proposes  |
| spend-spike-root-causer | 1.0.0 | Takes your cloud billing export (pasted CSV: date, service, cost, plus optional usage type, region, account an |
| sql-writer | 1.0.0 | Writes and optimizes SQL from what you paste into the chat |
| stack-trace-explainer | 1.0.0 | Explains a pasted error message or stack trace (Python, JavaScript/Node, Java/Kotlin, Go, Rust, Ruby, C#): the |
| standup-generator | 1.0.0 | Builds a team standup you can copy: for the people you name (or everyone active in the repositories you name,  |
| status-report-reality-check | 1.0.0 | Checks a status report against a ticket export, both pasted into the chat, and lists where the reported status |
| story-splitter | 1.0.0 | Splits an epic you paste into the chat into thin vertical user stories (as a / I want / so that), each with 2- |
| summarizer | 1.0.0 | Summarizes long text you paste into the chat (a meeting transcript, an email thread, a chat export, a document |
| team-pulse | 1.0.0 | Prepares a manager's 1:1s: for each person on the team (the people active in the repository or project you nam |
| terraform-plan-reviewer | 1.0.0 | Reviews a Terraform plan you paste into the chat (`terraform plan` text, `terraform show -json` or `terraform  |
| test-writer | 1.0.0 | Writes unit tests with edge cases for Python, JavaScript/TypeScript or Go code you paste into the chat: a func |
| thread-untangler | 1.0.1 | Untangles a Slack thread or an email chain you paste into the chat: it splits it into numbered messages with s |
| ticket-triager | 1.0.0 | Triages a batch of support tickets you paste into the chat (a CSV export, a JSON array, or blocks like 'Ticket |
| token-spend-analyzer | 1.0.0 | Analyzes an AI/LLM usage or spend export you paste into the chat: an OpenAI or Anthropic usage CSV, LiteLLM-st |
| variance-narrator | 1.0.0 | Writes the budget-versus-actual variance explanation finance asks for, from a table you paste into the chat (s |
| waiting-on-chaser | 1.0.0 | Finds what YOU asked other people for in your own sent email and chat over the last 14 days (3 to 30 if you as |
| who-knows-this-finder | 1.0.0 | Given a topic ("who understands the billing retry logic?"), names at most two people with the strongest eviden |
