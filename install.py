#!/usr/bin/env python3
"""Installs the Claude and Codex skills for this chat (standard library only).
  python install.py              both skills
  python install.py --claude     only ~/.claude/skills/claude-codex-chat
  python install.py --codex      only ~/.codex/skills/claude-codex-chat
  python install.py --statusline print the Claude Code statusLine snippet (live usage meter)
The skills get the absolute path of this folder written into them."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT_DIR = HERE.replace("\\", "/")
args = set(sys.argv[1:])
which = [w for w in ("claude", "codex") if f"--{w}" in args] or ["claude", "codex"]
homes = {"claude": os.path.join(os.path.expanduser("~"), ".claude"),
         "codex": os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")}

if "--statusline" in args:
    cmd = f'python "{CHAT_DIR}/claude_statusline.py"'
    print("Add this to ~/.claude/settings.json to feed the usage meter:\n")
    print(json.dumps({"statusLine": {"type": "command", "command": cmd}}, indent=2))
    sys.exit(0)

for who in which:
    src = os.path.join(HERE, "skills", who, "SKILL.md")
    dst = os.path.join(homes[who], "skills", "claude-codex-chat")
    os.makedirs(dst, exist_ok=True)
    text = open(src, encoding="utf-8").read().replace("{{CHAT_DIR}}", CHAT_DIR)
    with open(os.path.join(dst, "SKILL.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{who}: skill installed -> {dst}")
print(f"\nStart the chat:  python \"{CHAT_DIR}/server.py\"   then open http://127.0.0.1:8777")
