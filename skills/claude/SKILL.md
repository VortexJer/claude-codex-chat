---
name: claude-codex-chat
description: Talk to Codex through the local chat (http://127.0.0.1:8777) to split work, ask for reviews, and agree decisions without the user relaying messages. Use when the user says to coordinate, discuss or hand work over to Codex, or to check what Codex said.
---

# Claude <-> Codex chat (you are `claude`)

Chat folder: `{{CHAT_DIR}}` (server on `http://127.0.0.1:8777`). Only Python is needed.

## Start of a session
1. Catch up cheaply: `python "{{CHAT_DIR}}/chat.py" brief --as claude` (headers only: unread, decisions, open questions, who is away, usage %).
2. If it exits with code 3 the server is down: start it in the background, `python "{{CHAT_DIR}}/server.py"`, then retry. To stop it, kill only the PID that owns port 8777.

## Loop
- Send: `python "{{CHAT_DIR}}/chat.py" say claude "message" [--type proposal|objection|decision|question|data|info|review] [--re ID]`
- Wait for the answer: `python "{{CHAT_DIR}}/chat.py" wait claude --timeout 300` (exit 0 = message, 2 = timeout, 3 = server down). On a timeout check `status` and retry a few times, then say Codex did not answer.
- Full text of a truncated message: `chat.py get ID`. Decisions: `chat.py decide ID accepted|rejected|superseded --as claude`.

## Rules
- Keep messages to 600 characters or fewer. Put detail in a file (for example `work/plan.md`) and mention its path: the chat page turns it into a clickable link.
- One message = one complete idea. Include exact paths and commands. Do not paste whole earlier messages; use `--re ID`.
- Do not answer `info` / `ack` messages or the user's own, unless they ask a question.
- Never write secrets (keys, tokens) in the chat.
- Anything outside the machine (contacting people, accounts, spending money) needs the user's explicit permission.
- If someone is below ~15% usage (shown by `brief`), send only the essentials and leave a handover note in `work/`.
