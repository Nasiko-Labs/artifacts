"""The four tools this agent implements itself (everything else is MCP).

- `ask_human`        → pause INPUT_REQUIRED with a question (optionally options)
- `finish_task`      → final answer, task completes
- `analyze_conversation` → deterministic follow-up analysis (followup.py)
- `request_approval` → pause INPUT_REQUIRED with Approve / Edit / Cancel
"""

import json
from datetime import datetime

from followup import AnalysisInputError, analyze
from prompts import guidance_for
from send_guard import Approval

ASK_HUMAN = "ask_human"
FINISH_TASK = "finish_task"
ANALYZE_CONVERSATION = "analyze_conversation"
REQUEST_APPROVAL = "request_approval"

APPROVE = "Approve"
EDIT = "Edit"
CANCEL = "Cancel"
_APPROVE_WORDS = frozenset({"approve", "approved", "yes", "y", "ok", "go ahead"})
_CANCEL_WORDS = frozenset({"cancel", "no", "n", "stop"})

# Hard cap on the transcribed timeline: enough for a long thread on both
# platforms, small enough to keep one tool call's arguments sane.
MAX_ANALYZED_MESSAGES = 300

ASK_HUMAN_DEF = {
    "type": "function",
    "function": {
        "name": ASK_HUMAN,
        "description": (
            "Ask the user a question when you cannot proceed without their answer (no recipient "
            "or topic at all, or several unrelated conversations match). Pass `options` when the "
            "valid answers form a fixed set; you get back exactly the chosen `label`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "header": {"type": "string", "description": "Optional short title above options."},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },
                "multi_select": {"type": "boolean"},
                "allow_custom_input": {"type": "boolean"},
            },
            "required": ["question"],
        },
    },
}

FINISH_TASK_DEF = {
    "type": "function",
    "function": {
        "name": FINISH_TASK,
        "description": "Give the user your final message (context line + draft) and end the turn.",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
}

ANALYZE_CONVERSATION_DEF = {
    "type": "function",
    "function": {
        "name": ANALYZE_CONVERSATION,
        "description": (
            "Compute where a conversation stands from the messages you gathered across Gmail and "
            "Slack: who owes the next message, how many follow-ups the user already sent with no "
            "reply, how long they've waited, and the drafting guidance for this stage. Call this "
            "before writing any draft. Pass ALL related messages from both platforms in one call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "my_identities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Every identifier of the user: Gmail address(es), Slack user id, "
                        "Slack display name."
                    ),
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "author": {
                                "type": "string",
                                "description": "Sender email, Slack user id, or name — as the tool returned it.",
                            },
                            "timestamp": {
                                "type": "string",
                                "description": (
                                    "Exactly as returned: ISO 8601, epoch seconds/ms, Slack ts, "
                                    "or an email Date header."
                                ),
                            },
                            "text": {"type": "string"},
                            "platform": {"type": "string", "enum": ["gmail", "slack"]},
                            "channel": {
                                "type": "string",
                                "description": "Gmail thread id or Slack channel/thread id.",
                            },
                            "is_me": {
                                "type": "boolean",
                                "description": "Optional; set only if you are certain.",
                            },
                        },
                        "required": ["author", "timestamp", "text", "platform"],
                    },
                },
            },
            "required": ["my_identities", "messages"],
        },
    },
}

REQUEST_APPROVAL_DEF = {
    "type": "function",
    "function": {
        "name": REQUEST_APPROVAL,
        "description": (
            "Ask the user to approve one save/send action before performing it. Required before "
            "any tool that sends, posts or creates a draft — those are refused without it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["save_gmail_draft", "send_email", "post_slack_message"],
                },
                "target": {
                    "type": "string",
                    "description": "Recipient(s) + thread/channel, human readable.",
                },
                "recipient": {
                    "type": "string",
                    "description": (
                        "Exactly who the write goes to, as the write tool will receive it: the "
                        "email address(es), comma-separated, or the Slack channel/DM id. The write "
                        "is refused if it names anyone else (including cc/bcc)."
                    ),
                },
                "draft": {"type": "string", "description": "The exact body text that will be written."},
                "subject": {
                    "type": "string",
                    "description": "Email subject exactly as the write tool will receive it, if any.",
                },
                "tool_name": {
                    "type": "string",
                    "description": (
                        "Exact name of the tool that will perform the write (as found with "
                        "nasiko_search_tools). Only this tool will be allowed to run. Choose it by "
                        "reading its description: for Slack, the one that posts a normal message to "
                        "a channel/DM; for Gmail, the one that creates a draft or sends an email."
                    ),
                },
            },
            "required": ["action", "target", "recipient", "draft", "tool_name"],
        },
    },
}

LOCAL_TOOL_DEFS = (ASK_HUMAN_DEF, FINISH_TASK_DEF, ANALYZE_CONVERSATION_DEF, REQUEST_APPROVAL_DEF)

_ACTION_LABELS = {
    "save_gmail_draft": "save this as a Gmail draft",
    "send_email": "send this email",
    "post_slack_message": "post this to Slack",
}


def analyze_conversation_result(arguments: dict, now: datetime) -> str:
    """Tool result for `analyze_conversation` — the analysis, or an error the
    model can act on (never an exception: a bad transcription is the model's
    to fix, not a task failure)."""
    messages = arguments.get("messages")
    identities = arguments.get("my_identities")
    if not isinstance(messages, list):
        return _error("`messages` must be an array of message objects")
    if not isinstance(identities, list) or not identities:
        return _error("`my_identities` must list the user's Gmail address and/or Slack user id")
    if len(messages) > MAX_ANALYZED_MESSAGES:
        return _error(
            f"{len(messages)} messages is more than {MAX_ANALYZED_MESSAGES}; keep only the "
            "messages related to this conversation"
        )
    try:
        analysis = analyze(messages, [str(i) for i in identities], now)
    except AnalysisInputError as e:
        return _error(str(e))
    return json.dumps({**analysis.to_dict(), "stage_guidance": guidance_for(analysis.stage)})


def approval_request(arguments: dict, tool_description: str = "") -> tuple[str, dict, Approval]:
    """(question, HITL selectable-options metadata, the approval it would grant).
    The card names the tool and what it does, so the user approves how the
    message goes out, not just its text."""
    action = str(arguments.get("action") or "")
    target = str(arguments.get("target") or "")
    draft = str(arguments.get("draft") or "")
    subject = str(arguments.get("subject") or "")
    tool = str(arguments.get("tool_name") or "")
    recipient = str(arguments.get("recipient") or "")
    label = _ACTION_LABELS.get(action, action.replace("_", " "))
    to_line = f"To: {recipient}\n" if recipient else ""
    subject_line = f"{to_line}Subject: {subject}\n\n" if subject else (f"{to_line}\n" if to_line else "")
    what_it_does = f" — {_first_sentence(tool_description)}" if tool_description else ""
    question = (
        f"Ready to {label} → {target}:\n\n{subject_line}{draft}\n\n"
        f"Using: {tool}{what_it_does}\n\nGo ahead?"
    )
    options = {
        "header": "Approve",
        "options": [
            {"label": APPROVE, "description": "Do it exactly as shown"},
            {"label": EDIT, "description": "Type what to change"},
            {"label": CANCEL, "description": "Don't save or send anything"},
        ],
        "multi_select": False,
        "allow_custom_input": True,
    }
    approval = Approval(
        action=action, target=target, draft=draft, tool=tool, recipient=recipient, subject=subject
    )
    return question, options, approval


def _first_sentence(text: str, limit: int = 200) -> str:
    sentence = text.strip().split(". ")[0].rstrip(".")
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def interpret_approval_answer(answer: str, pending: Approval) -> tuple[Approval | None, str]:
    """(approval to grant or None, tool result for the model)."""
    normalized = answer.strip().lower().rstrip(".!")
    if normalized in _APPROVE_WORDS:
        return pending, json.dumps({
            "status": "approved",
            "instruction": (
                "Perform exactly this one action now with exactly the approved text, "
                "then call finish_task."
            ),
        })
    if normalized in _CANCEL_WORDS:
        return None, json.dumps({
            "status": "cancelled",
            "instruction": "Do not write anything. Call finish_task confirming nothing was saved or sent.",
        })
    if normalized == EDIT.lower():
        return None, json.dumps({
            "status": "changes_requested",
            "instruction": "Call ask_human to ask what the user wants changed, then revise and request approval again.",
        })
    return None, json.dumps({
        "status": "changes_requested",
        "feedback": answer.strip(),
        "instruction": (
            "Revise the draft per this feedback, then call request_approval again with the new text."
        ),
    })


def _error(message: str) -> str:
    return json.dumps({"error": message})
