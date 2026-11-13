---
name: claude-codex-chat
description: Talk to Claude through the local chat (http://127.0.0.1:8777) to split work, ask for reviews and agree decisions. Use when the user asks you to coordinate, discuss or hand work over to Claude, or to read what Claude said.
---

# Claude <-> Codex chat (you are `codex`)

Chat folder: `{{CHAT_DIR}}` (server on `http://127.0.0.1:8777`). Only Python is needed.

## Start of a session
1. `python "{{CHAT_DIR}}/chat.py" brief --as codex`: short catch-up (headers only).
2. Exit code 3 means the server is down: start it in the background with `python "{{CHAT_DIR}}/server.py"` and retry.

## Loop
1. Read: `python "{{CHAT_DIR}}/chat.py" read codex` (1 message per call).
2. Answer: `python "{{CHAT_DIR}}/chat.py" say codex "message" [--type proposal|objection|decision|question|data|info|review] [--re ID]` (`-` as the text reads stdin).
3. Wait: `python "{{CHAT_DIR}}/chat.py" wait codex --timeout 300` (0 = message, 2 = timeout, 3 = server down). After a timeout check `status`; retry up to ~3 times and then report that Claude did not appear.
- Full text of a truncated message: `chat.py get ID`. Decide: `chat.py decide ID accepted|rejected|superseded --as codex`.
- Out of usage? `chat.py away codex --until 21:00 --note "no usage" --as codex`, and `--clear` when back.

## Rules
- At most 600 characters per message; detail goes in a file and its path is mentioned (clickable in the chat page).
- One message = one complete idea, with exact paths and commands. Reply with `--re ID`, do not quote whole messages.
- Do not answer `info` / `ack` messages or the user's, unless it is a question.
- Never write secrets in the chat. Nothing external (contacts, accounts, spending) without the user's permission.
