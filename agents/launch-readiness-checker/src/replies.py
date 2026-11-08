"""Fixed replies and notes: written once, never by the model."""

NO_TRACE_CONTEXT = ("I can't run this request: it arrived without the platform trace context my model and tool "
                    "calls need. Please send it again through the Nasiko chat.")
GATEWAY_UNREACHABLE = ("I couldn't reach the platform's tool gateway, so I can't read the tracker, docs, support "
                       "desk or task manager right now. Please try again in a minute.")
ASK_LAUNCH_NAME = "Which launch should I check?"
ASK_LAUNCH_TEXT = ("Which launch should I check? Type its name (for example \"Dashboards 2.0\"), optionally with "
                   "a date (\"on Nov 20\") and hints like `label: dash2`.")
CANCEL = "Cancel"
CANCELLED = "OK, I stopped. Ask again whenever you want to check a launch."
STEP_LIMIT_NOTE = "I hit my step limit while reading {area}; that area uses what I had read by then."
UNREADABLE_ANSWER = "I couldn't read a usable answer from the model this time. Please try again."
READ_ONLY_REPLY = ("I'm read-only: I never edit an issue, a task, a document or a macro. Make that change in the "
                   "tool itself, then ask me to check the launch again.")
NO_RESULT_YET = "There is no readiness result in this conversation yet. Ask me to check a launch first."
TIMED_OUT = ("I ran out of my time budget for this turn before finishing. Try again, or give a label or project "
             "hint so the searches are narrower.")
NO_SCHEDULE = "I can't run on a schedule; ask me again whenever you want a fresh check."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
PASTE_TIP = ("You can paste a checklist for an area I can't read, e.g. `marketing:` then lines like "
             "`- [x] Blog post` or `- [ ] Launch email due 2026-11-18 @dana`.")
