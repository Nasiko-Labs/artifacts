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

## Agents (162)

`<name>-stateless` agents keep nothing between messages (no state API, no pauses) and use the chat
history Nasiko attaches as their memory, so any replica can serve any message; they are built on
LangGraph, LangChain or CrewAI (see each AgentCard's `agentFramework`). `<name>-rust` agents are
stateless Rust builds (single static binary, image from `scratch`).

| Name | Version | What it does |
| --- | --- | --- |
| access-review | 1.0.0 | Reviews who has prod or admin access against your org roster |
| account-news | 1.0.1 | Reports what changed at one company in the last seven days that a renewal owner should know: product announcem |
| adhoc-request-deflector | 1.0.0 | Checks whether an existing dashboard already answers an ad-hoc data request ("can you pull weekly active users |
| alert-noise-classifier | 1.0.0 | Classifies a list of fired alerts you paste into the chat (an incident-tool CSV export, Alertmanager or Grafan |
| alert-rule-reviewer | 1.0.0 | Reviews alert configuration you paste into the chat (Prometheus rule files, Alertmanager config, Datadog monit |
| alert-tuner | 1.0.0 | Finds noisy alerts from your incident pager's history and proposes how to quiet them |
| app-store-review-miner | 1.0.0 | Mines app store and Google Play reviews you paste (blocks, CSV/TSV or JSON; up to 400 reviews a turn): groups  |
| billing-export-analyzer | 1.0.0 | Analyzes a cloud billing / cost export you paste into the chat (CSV with a header row: date, service, cost, pl |
| blast-radius-reviewer | 1.0.0 | Reviews a code diff you paste (git diff, PR  |
| board-deck-fact-checker | 1.0.3 | Fact-checks a board deck against its source spreadsheets: it reads the deck (a file link or name, or pasted sl |
| board-metrics-updater | 1.0.0 | Computes your company's subscription revenue metrics for a period (default: last full calendar month; also a q |
| bug-to-customer-notifier | 1.0.0 | When a bug in your tracker is Done or Released, confirms the fix actually shipped (a published, non-draft rele |
| category-briefing | 1.0.3 | A CrewAI crew (researcher, then editor) that reads the market this week: what enterprise buyers are reacting t |
| chart-maker | 1.0.1 | Turns data you paste into a chart, or a process or structure you describe into a diagram, in a form the chat c |
| chunker-merger | 1.0.0 | Cuts a long text into numbered, ready-to-paste parts and merges the per-part JSON results back into one result |
| chunker-merger-rust | 1.0.0 | Cuts a long text into numbered, ready-to-paste parts and merges the per-part JSON results back into one result |
| chunker-merger-stateless | 1.0.0 | Cuts a long text into numbered, ready-to-paste parts and merges the per-part JSON results back into one result |
| churn-early-warning | 1.0.0 | Ranks which paying customers might churn, on request: it reads your payments data for cancellations, scheduled |
| ci-failure-detective | 1.0.0 | Tells you why a CI run failed |
| cluster-health | 1.0.0 | Read-only Kubernetes triage from kubectl output you paste (get pods -A, get nodes, get events -A, top nodes/po |
| code-reviewer | 1.0.0 | Reviews a code diff you paste (unified diff, e |
| code-reviewer-stateless | 1.0.0 | Reviews a code diff you paste (unified diff, e.g. from git diff) and gives review comments by severity (critic |
| commitment-advisor | 1.0.0 | Advises how much cloud commitment (savings plans, reserved instances) to buy and for how long, from hourly or  |
| commitment-audit | 1.0.0 | Audits a team's committed work for a quarter |
| comms-followup | 0.3.0 | Writes the next message on an existing thread (email or Slack), such as a renewal follow-up |
| competitor-change-watcher | 1.0.0 | Finds what changed in competitor changelogs and pricing pages you paste (one block per competitor and source,  |
| config-drift-detector | 1.0.0 | Finds configuration drift: where live infrastructure no longer matches its infrastructure-as-code |
| config-explainer | 1.0.0 | Explains a pasted regular expression, cron schedule, AWS IAM policy or nginx configuration in plain English an |
| context-aware-pr-reviewer | 1.0.0 | Reviews one pull request against what it was supposed to do, not just the diff |
| contract-review | 1.1.0 | Reviews an MSA against the company playbook: one pass or break per playbook rule, quoting the contract clause  |
| csv-to-insight | 1.0.0 | Turns a CSV table you paste into the chat (sales, signups, tickets, any table with a header row) into key find |
| customer-call-miner | 1.0.0 | Mines this week's recorded customer calls: reads each external call's transcript, matches the call to its CRM  |
| cycle-retrospective | 1.0.0 | Builds a retrospective for one tracker cycle (the last finished one, the current one, or a cycle you name): pl |
| dashboard-trust-checker | 1.0.0 | Audits a dashboard inventory you provide (a spreadsheet or a pasted export with name, owner, last refresh, vie |
| day-planner | 1.0.0 | Turns your own calendar into a day plan: today (or a day you name, up to 5 days) as an ordered agenda with eac |
| decision-log-builder | 1.0.0 | Scans your chat channels over the last 7 days (1 to 14 if you ask, or since Monday for 'this week') for decisi |
| decision-memo-writer | 1.0.0 | Writes a one-page decision memo from the decision, options, constraints, criteria and data you PASTE: context, |
| decision-memo-writer-stateless | 1.0.0 | Writes a one-page decision memo from the decision, options, constraints, criteria and data you PASTE: context, |
| deck-builder | 1.0.0 | Turns your notes, an outline or pasted data into a real, editable PowerPoint deck (3-20 slides): action-title  |
| dependency-risk-scanner | 1.0.0 | Tells you which of your repositories are exposed to a dependency advisory you paste (a CVE or GHSA id with the |
| design-doc-drafter | 1.0.0 | Turns requirements you paste into the chat into a software design document with a fixed structure: context and |
| design-doc-drafter-stateless | 1.0.0 | Turns requirements you paste into the chat into a software design document with a fixed structure: context and |
| doc-converter | 1.0.0 | Turns a document into clean Markdown: PDF (text layer), Word, PowerPoint, Excel, CSV, HTML, JSON, XML, EPUB, p |
| drafter | 1.0.0 | Writes one message from you to a specific person or group, in your tone and in the right register for where yo |
| drafter-stateless | 1.0.0 | Writes one message from you to a specific person or group, in your tone and in the right register for where yo |
| enterprise-deal-reconciler | 1.0.0 | Reconciles an enterprise contract or order form with its billing setup before the first invoice goes out |
| escalation-predictor | 1.0.0 | Scores your open support-desk tickets for the risk that they escalate (priority, age, customer tier, unanswere |
| escalation-summarizer | 1.0.0 | Turns a messy escalation thread you paste (ticket comments, a chat or email chain, internal notes) into what t |
| escalation-summarizer-stateless | 1.0.0 | Turns a messy escalation thread you paste (ticket comments, a chat or email chain, internal notes) into what t |
| exec-brief-writer | 1.0.0 | Turns the outputs of other agents (or your own notes) that you PASTE into one conversation into a one-page exe |
| exec-brief-writer-stateless | 1.0.0 | Turns the outputs of other agents (or your own notes) that you PASTE into one conversation into a one-page exe |
| failed-payment-rescuer | 1.0.0 | Finds failed payments in your own payments account over the last 7 days (1 to 30 if you ask, or since your las |
| feedback-clusterer | 1.0.0 | Groups customer feedback you paste into the chat (support tickets, survey answers, app reviews, call notes; a  |
| feedback-to-roadmap | 1.0.0 | Pulls recent customer feedback from your support desk and a feedback channel in chat (plus anything you paste) |
| finance-rollups | 1.0.0 | Month-end close view from your own payments account (default: the last 3 full calendar months, UTC; also "last |
| fix-this-test | 1.0.0 | Fixes one failing Python (pytest) test |
| flaky-test-hunter | 1.0.0 | Finds the flaky tests in a repository's recent CI history |
| formatter | 1.0.0 | Formats structured output (a JSON envelope from another agent, or any JSON) as Markdown, Slack Block Kit block |
| formatter-rust | 1.0.0 | Formats structured output (a JSON envelope from another agent, or any JSON) as Markdown, Slack Block Kit block |
| formatter-stateless | 1.0.0 | Formats structured output (a JSON envelope from another agent, or any JSON) as Markdown, Slack Block Kit block |
| hiring-loop-agent | 1.0.0 | Prepares a hiring debrief for ONE candidate: it gathers the interviewers' written feedback (pasted scorecards, |
| humanizer | 1.0.0 | Takes a draft you paste (an email, a message, a memo, a post, or another agent's draft) and returns the same d |
| humanizer-stateless | 1.0.0 | Takes a draft you paste (an email, a message, a memo, a post, or another agent's draft) and returns the same d |
| iac-cost-gate | 1.0.0 | Estimates the monthly AWS cost of Terraform HCL, a terraform show -json plan or a CloudFormation template you  |
| idle-resource-reaper | 1.0.0 | Reviews a cloud resource inventory you paste (CSV, or the JSON of the standard describe commands for volumes,  |
| inbox-to-linear | 1.0.0 | Scans your recent inbox (last 3 days by default, 1 to 14 if you ask) for emails that are really work requests: |
| inbox-triage | 1.0.0 | Reads your recent inbox (last 2 days by default, 1 to 14 if you ask) and sorts each thread into urgent (needs  |
| incident-copilot | 1.0.0 | On-call incident copilot |
| insight-narrator | 1.0.0 | Explains a small table in plain English for people who don't read tables |
| invoice-chaser | 1.0.0 | Lists your open customer invoices that are past their due date from your payments account, checks your own sen |
| issue-hygiene-bot | 1.0.0 | Checks your tracker for issue hygiene problems: open issues with no project, no estimate (only for teams that  |
| issue-localizer | 1.0.0 | Finds where one software issue most likely lives in a repository: a ranked list of files, then the functions o |
| issue-to-pr | 1.0.1 | Turns one small, well-described software issue into a draft pull request |
| kudos-collector | 1.0.0 | Collects concrete, linked evidence of good work for the people you name (up to 8), or for the people who merge |
| kudos-collector-offline | 1.0.0 | Collects concrete, evidenced recognition examples per person from a PR list, Slack praise and shipped features |
| launch-readiness-checker | 1.0.0 | Checks whether one named launch is ready, across four areas: engineering (open, urgent, blocked and unowned is |
| macro-gap-finder | 1.0.0 | Finds the recurring manual answers in support replies you paste (CSV, JSON or plain text, 10 to 2,000 replies) |
| macro-gap-finder-live | 1.0.0 | Reads the agent replies on recently solved tickets in your connected support desk (default last 30 days, up to |
| meeting-notes-processor | 1.0.0 | Turns a meeting transcript or raw meeting notes you paste into structured notes: the attendees with their shar |
| meeting-notes-processor-rust | 1.0.0 | Turns a meeting transcript or raw meeting notes you paste into structured notes: the attendees with their shar |
| meeting-notes-processor-stateless | 1.0.0 | Turns a meeting transcript or raw meeting notes you paste into structured notes: the attendees with their shar |
| meeting-prep | 2.0.0 | Two jobs |
| meeting-roi | 1.0.0 | Audits your recurring meetings over the last 4 weeks (7 to 56 days if you ask): for each series it reads your  |
| metric-mover-offline | 1.0.0 | Explains why a metric moved from a before/after table you paste into the chat, broken down by segment (channel |
| metrics-pull | 1.0.0 | Pulls fresh numbers from a spreadsheet you name (title or link, optional tab and range) or from a table you pa |
| onboarding-guide-generator | 1.0.0 | Builds a repository walkthrough for a new hire and their first ticket, from what the repository actually conta |
| one-on-one-prep | 1.0.0 | Prepares a manager's next 1:1 with one report from the last meeting's notes and the recent updates the manager |
| opportunity-brief | 1.0.0 | Turns public signals into a short, cited opportunity brief on a topic you name (a market, a technology, a comp |
| performance-review-drafter | 1.0.0 | Drafts a performance review for one person from the examples and feedback a manager pastes into the chat |
| postmortem-follow-through | 1.0.0 | Finds the postmortems from the last 6 months (up to 12 if you ask) in your tracker, and in your postmortem doc |
| postmortem-writer | 1.0.0 | Turns an incident timeline or chat log you paste (a chat thread, pager notes, a bullet timeline) into a blamel |
| pr-agent | 1.0.1 | Reviews GitHub pull requests, like PR-Agent: give it a PR link or owner/repo#123 and a command |
| pr-description-writer | 1.0.0 | Turns a diff you paste into the chat (git diff,  |
| pr-description-writer-stateless | 1.0.0 | Turns a diff you paste into the chat (git diff, .patch or git diff --stat) into a pull request draft: a conven |
| pr-review-queue | 1.0.0 | Shows the pull request review queue for the repositories you name (owner/repo, up to 5): which open pull reque |
| prd-critic | 1.0.0 | Reviews a PRD you paste into the chat and returns its gaps: missing sections (problem, users, goals, non-goals |
| prd-critic-stateless | 1.0.0 | Reviews a PRD you paste into the chat and returns its gaps: missing sections (problem, users, goals, non-goals |
| prd-writer | 1.0.0 | Turns a product brief you paste into the chat (the idea, who it is for, constraints, any data) into a PRD with |
| prd-writer-stateless | 1.0.0 | Turns a product brief you paste into the chat (the idea, who it is for, constraints, any data) into a PRD with |
| pre-mortem | 1.0.0 | Runs a pre-mortem on a plan, PRD or brief you paste into the chat: it assumes the plan already failed and writ |
| pre-mortem-stateless | 1.0.0 | Runs a pre-mortem on a plan, PRD or brief you paste into the chat: it assumes the plan already failed and writ |
| prioritizer | 1.0.0 | Ranks a task list you paste into the chat (free text, bullets, or another agent's action items), with optional |
| prioritizer-stateless | 1.0.0 | Ranks a task list you paste into the chat (free text, bullets, or another agent's action items), with optional |
| promise-extractor | 1.0.0 | Finds the commitments you made in text you paste (your sent emails, a chat export, meeting notes): "I'll send  |
| promise-tracker | 1.0.3 | Finds what YOU committed to in your own sent email and chat over the last 7 days (up to 14 if you ask): "I'll  |
| query-explainer | 1.0.0 | Explains SQL you paste into the chat: what the query computes, step by step in plain English, the assumptions  |
| question-deduplicator | 1.0.0 | Before you ask a question in team chat, checks whether it was already asked and answered there |
| refund-dispute-handler | 1.0.0 | Handles a refund request or a dispute (chargeback) against your own payments account: given a customer, a char |
| release-notes-writer | 1.0.0 | Turns the merged PR titles, commits or shipped issues you paste into customer-facing release notes grouped by  |
| release-notes-writer-stateless | 1.0.0 | Turns the merged PR titles, commits or shipped issues you paste into customer-facing release notes grouped by  |
| reply-drafter | 1.0.0 | Drafts your reply to a message you received, in your own voice |
| reply-drafter-rust | 1.0.0 | Drafts your reply to a message you received, in your own voice. Paste the incoming email, chat or text message |
| reply-drafter-stateless | 1.0.0 | Drafts your reply to a message you received, in your own voice. Paste the incoming email, chat or text message |
| research | 1.1.0 | Reports a company's public GitHub footprint: its top public repositories, the languages they use, and which re |
| research-synthesizer | 1.0.0 | Synthesizes a pile of sources you paste into the chat (interview notes, articles, reports, forum threads, call |
| revenue-anomaly-explainer | 1.0.0 | Checks a day of subscription revenue movement in your own payments account (default: yesterday, UTC; also a na |
| runbook-generator | 1.0.0 | Writes a step-by-step operational runbook from a service description (what it is, what it depends on, how it f |
| runbook-generator-stateless | 1.0.0 | Writes a step-by-step operational runbook from a service description (what it is, what it depends on, how it f |
| scope-creep-detector | 1.0.0 | Shows how a team's sprint (or cycle) scope changed after it started: it compares what was committed at the sta |
| security-gate | 1.0.0 | Security gate for one pull request: runs a bundled static-analysis ruleset (SQL and command injection, eval, u |
| security-reviewer | 1.0.0 | Reviews code or a diff you paste into the chat for security problems: injection, authentication and authorizat |
| seo-audit | 1.0.0 | Audits one web page for on-page SEO and for readiness to be cited by AI answer engines |
| sheet-watcher | 1.0.0 | Watches a tracking spreadsheet on demand: reads the sheet you name (or a table you paste), compares it in code |
| shift-handoff-writer | 1.0.0 | Turns the day's notes you paste into the chat (queue notes, ticket updates, chat jottings, a running log) into |
| shift-handoff-writer-stateless | 1.0.0 | Turns the day's notes you paste into the chat (queue notes, ticket updates, chat jottings, a running log) into |
| slack-catch-up | 1.0.0 | Catches you up on your team chat workspace: for the channels and direct messages you are in, since your last c |
| slack-thread-to-issue | 1.0.0 | Scans your chat channels (the ones you name, else your 8 most active; last 3 days by default, 1 to 14 if you a |
| slo-burn-forecaster | 1.0.0 | Forecasts SLO error-budget burn from SLI data you paste (timestamped good/total, errors/total or error-rate ro |
| social-post-drafter | 1.0.0 | Turns an announcement, notes, a pasted article or a one-line idea into ready-to-copy public posts for LinkedIn |
| social-post-drafter-stateless | 1.0.0 | Turns an announcement, notes, a pasted article or a one-line idea into ready-to-copy public posts for LinkedIn |
| spec-to-issues-breaker | 1.0.0 | Reads a product spec or PRD (a document found by title or link, or text you paste after "Spec:") and proposes  |
| spend-spike-root-causer | 1.0.0 | Takes your cloud billing export (pasted CSV: date, service, cost, plus optional usage type, region, account an |
| sql-writer | 1.0.0 | Writes and optimizes SQL from what you paste into the chat |
| stack-trace-explainer | 1.0.0 | Explains a pasted error message or stack trace (Python, JavaScript/Node, Java/Kotlin, Go, Rust, Ruby, C#): the |
| stack-trace-explainer-rust | 1.0.0 | Explains a pasted error message or stack trace (Python, JavaScript/Node, Java/Kotlin, Go, Rust, Ruby, C#): the |
| stack-trace-explainer-stateless | 1.0.0 | Explains a pasted error message or stack trace (Python, JavaScript/Node, Java/Kotlin, Go, Rust, Ruby, C#): the |
| standup-generator | 1.0.0 | Builds a team standup you can copy: for the people you name (or everyone active in the repositories you name,  |
| status-report-reality-check | 1.0.0 | Checks a status report against a ticket export, both pasted into the chat, and lists where the reported status |
| story-splitter | 1.0.0 | Splits an epic you paste into the chat into thin vertical user stories (as a / I want / so that), each with 2- |
| summarizer | 1.0.0 | Summarizes long text you paste into the chat (a meeting transcript, an email thread, a chat export, a document |
| summarizer-rust | 1.0.0 | Summarizes long text you paste into the chat (a meeting transcript, an email thread, a chat export, a document |
| summarizer-stateless | 1.0.0 | Summarizes long text you paste into the chat (a meeting transcript, an email thread, a chat export, a document |
| support-digest | 1.0.0 | Builds a support digest for a period (default the last 7 days) from your support desk: ticket volume with day- |
| task-breakdown | 1.0.0 | Turns a goal, spec or messy brief you type or paste into a structured work plan: 3-6 phases named by outcome,  |
| task-breakdown-rust | 1.0.0 | Turns a goal, spec or messy brief you type or paste into a structured work plan: 3-6 phases named by outcome,  |
| task-breakdown-stateless | 1.0.0 | Turns a goal, spec or messy brief you type or paste into a structured work plan: 3-6 phases named by outcome,  |
| team-pulse | 1.0.0 | Prepares a manager's 1:1s: for each person on the team (the people active in the repository or project you nam |
| terraform-blast-radius | 1.0.0 | Shows the blast radius of a Terraform plan you paste (the text of terraform plan or terraform show -json) plus |
| terraform-plan-reviewer | 1.0.0 | Reviews a Terraform plan you paste into the chat (`terraform plan` text, `terraform show -json` or `terraform  |
| test-writer | 1.0.0 | Writes unit tests with edge cases for Python, JavaScript/TypeScript or Go code you paste into the chat: a func |
| thread-untangler | 1.0.1 | Untangles a Slack thread or an email chain you paste into the chat: it splits it into numbered messages with s |
| ticket-triager | 1.0.0 | Triages a batch of support tickets you paste into the chat (a CSV export, a JSON array, or blocks like 'Ticket |
| ticket-triager-stateless | 1.0.0 | Triages a batch of support tickets you paste into the chat (a CSV export, a JSON array, or blocks like 'Ticket |
| token-spend-analyzer | 1.0.0 | Analyzes an AI/LLM usage or spend export you paste into the chat: an OpenAI or Anthropic usage CSV, LiteLLM-st |
| translator | 1.0.0 | Translates text you paste into about 40 target languages (Spanish, French, German, Portuguese, Japanese, Chine |
| translator-rust | 1.0.0 | Translates text you paste into about 40 target languages (Spanish, French, German, Portuguese, Japanese, Chine |
| translator-stateless | 1.0.0 | Translates text you paste into about 40 target languages (Spanish, French, German, Portuguese, Japanese, Chine |
| variance-narrator | 1.0.0 | Writes the budget-versus-actual variance explanation finance asks for, from a table you paste into the chat (s |
| waiting-on-chaser | 1.0.0 | Finds what YOU asked other people for in your own sent email and chat over the last 14 days (3 to 30 if you as |
| weekly-leadership-rollup | 1.0.0 | Builds a cross-team weekly status rollup a leader can read in two minutes: for each tracker team you can see ( |
| weekly-numbers-narrator | 1.0.0 | Reads your weekly metrics spreadsheet (or a pasted table), computes in code what changed in the latest period  |
| who-knows-this-finder | 1.0.0 | Given a topic ("who understands the billing retry logic?"), names at most two people with the strongest eviden |
