# Comms Follow-up Assistant

A Nasiko A2A agent that drafts emails and Slack messages from your real conversation history.
Tell it who and what, e.g. *"draft a message to Priya about the Q3 invoice"*. It then pulls the
related threads from **both Gmail and Slack** (DMs, group DMs, channels) through the Nasiko MCP gateway,
works out how many times you have already followed up without a reply, and writes the draft to fit.

| Where the conversation stands | Draft it writes |
| --- | --- |
| They wrote last | A reply that answers their points |
| Your ask, no reply | A friendly, short first nudge |
| One nudge already unanswered | Acknowledges the earlier nudge; an easy-to-answer ask with a date |
| 2+ nudges unanswered (any mix of email + Slack) | A change of approach: simpler ask, a call, another owner, or "here's what I'll assume by <date>" |

It never repeats the wording of your earlier nudges. It **never saves or sends anything without your
approval** of the exact text.

## How it works

```
request ──► find tools (nasiko_search_tools) ──► Gmail + Slack search & full threads
              ▲                                   (+ optional web search/scrape)
              │                                              │
              │                       analyze_conversation (deterministic: who owes,
              │                       nudge count, dates, cross-channel) ──► stage
              │                                              │
              └── request_approval ◄── "save it" ◄── draft (stage-guided) ──► finish_task
                   (Approve/Edit/Cancel)  then exactly one write, verified against the approved text
```

| File | Role |
| --- | --- |
| `src/followup.py` | Pure follow-up analysis. Counting and dating is done in code, not by the LLM |
| `src/send_guard.py` | Refuses any write tool (send/post/create draft/delete…) unless approved, and only with the approved text; one approval allows one write |
| `src/tool_catalog.py` | Runtime tool discovery. Web tools are recognised by capability, not by provider |
| `src/prompts.py` | Workflow, escalation ladder, per-platform style |
| `src/conversation_loop.py` | The LLM tool loop and pause/resume state |
| `src/agent_executor.py` | A2A plumbing (from `general-assistant`): credentials, traceparent, HITL |
| `src/mcp_client.py`, `src/telemetry.py` | Copied verbatim from `general-assistant` |

## Connecting Slack, Gmail and web search

All of these are **MCP connectors in Nasiko**. The agent contains no credentials and no tool names.

1. Connect **Slack** and **Gmail** in Nasiko (MCP connectors → OAuth), and grant both to this agent.
2. Optional: connect a web search/scrape provider (**Anakin**, **Firecrawl**, or any other) as an MCP
   connector and grant it. The agent finds it by asking the gateway for "search the web" / "scrape a
   web page". Swapping providers needs no code change. Web calls are capped at 5 per task.
3. If a source isn't connected, the agent drafts from the others and says what was missing.

## Environment

| Var | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | Required for real use |
| `MODEL` / `OPENAI_MODEL` | `gpt-4o` | Any tool-calling chat model |
| `OPENAI_BASE_URL` | — | Optional OpenAI-compatible endpoint |
| `MCP_GATEWAY_URL`, `MCP_GATEWAY_TOKEN` | — | Injected by Nasiko at deploy |
| `PORT` | `8000` | |

## Develop

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest          # hermetic: no network, no OpenAI, no Nasiko
cd src && OPENAI_API_KEY=... ../.venv/bin/python main.py
```

Deploy: `nasiko upload` from this folder (or `docker build` + `nasiko push`/`deploy`).

## Example prompts

- "Draft a follow-up to Priya about the Q3 invoice"
- "I've chased Rahul about the API keys — write the next message on Slack"
- "Reply to the email from legal about the NDA; check their public announcement first"
- "Save that as a Gmail draft" / "Post it in the thread"
