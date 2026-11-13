"""Cliente CLI del chat. Solo libreria estandar.
  python chat.py say  <yo> "texto" [--to claude|codex|user|all]    (texto '-' = lee stdin)
  python chat.py wait <yo> [--timeout 300]   espera mensajes nuevos para <yo> (sale con codigo 2 si timeout)
  python chat.py read <yo> [--peek]          no leidos ahora, sin esperar
  python chat.py log [--last N]              historial completo
  python chat.py status                      quien esta conectado/esperando
<yo> = claude | codex | user. Servidor: env CHAT_URL (def. http://127.0.0.1:8777)"""
import json, os, sys, time, argparse, urllib.request, urllib.error

URL = os.environ.get("CHAT_URL", "http://127.0.0.1:8777")
OTHER = {"claude": "codex", "codex": "claude", "user": "all"}


def http(path, data=None, timeout=70):
    req = urllib.request.Request(
        URL + path, method="POST" if data else "GET",
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def show(ms):
    for m in ms:
        t = time.strftime("%H:%M:%S", time.localtime(m["ts"]))
        print(f"[#{m['id']} {t} {m['from']} -> {m['to']}]\n{m['text']}\n")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("who", nargs="?")
    ap.add_argument("text", nargs="?")
    ap.add_argument("--to")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--peek", action="store_true")
    a = ap.parse_args()
    try:
        if a.cmd == "say":
            text = sys.stdin.read() if a.text == "-" else a.text
            m = http("/api/messages", {"from": a.who, "to": a.to or OTHER.get(a.who, "all"), "text": text})
            print(f"enviado #{m['id']}")
        elif a.cmd in ("wait", "read"):
            end = time.time() + (a.timeout if a.cmd == "wait" else 0)
            peek = "&peek=1" if a.peek else ""
            while True:
                left = max(0, end - time.time())
                r = http(f"/api/inbox?who={a.who}&wait={min(left, 25)}{peek}")
                if r["messages"]:
                    show(r["messages"])
                    return 0
                if time.time() >= end:
                    if a.cmd == "wait":
                        print("TIMEOUT: sin mensajes nuevos")
                        return 2
                    print("sin mensajes nuevos")
                    return 0
        elif a.cmd == "log":
            ms = http("/api/messages")["messages"]
            show(ms[-a.last:] if a.last else ms)
        elif a.cmd == "status":
            for p, v in http("/api/messages")["presence"].items():
                if p == "user":
                    continue
                s = "nunca visto" if v["seen_ago"] is None else f"visto hace {int(v['seen_ago'])}s"
                print(f"{p}: {s}{' (ESPERANDO)' if v['waiting'] else ''}")
        else:
            print(__doc__)
            return 1
    except urllib.error.URLError as e:
        print(f"ERROR: servidor no responde en {URL} ({e}). Arranca: python server.py")
        return 3
    return 0


sys.exit(main())
