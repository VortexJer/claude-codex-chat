"""Chat compartido Claude <-> Codex <-> Usuario. Solo libreria estandar.
Uso: python server.py [puerto]   (por defecto 8777)"""
import json, os, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "messages.jsonl")
STATE = os.path.join(HERE, "state.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
PEOPLE = ("claude", "codex", "user")

cond = threading.Condition()
messages = []
cursors = {p: 0 for p in PEOPLE}     # ultimo id entregado a cada participante
presence = {p: 0.0 for p in PEOPLE}  # ultima vez visto (epoch)
waiting = {p: 0 for p in PEOPLE}     # esta bloqueado en un wait

if os.path.exists(LOG):
    with open(LOG, encoding="utf-8") as f:
        messages = [json.loads(l) for l in f if l.strip()]
if os.path.exists(STATE):
    try:
        cursors.update(json.load(open(STATE, encoding="utf-8")))
    except Exception:
        pass


def save_state():
    json.dump(cursors, open(STATE, "w", encoding="utf-8"))


def unread_for(who):
    return [m for m in messages if m["id"] > cursors[who] and m["from"] != who
            and m["to"] in (who, "all")]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        g = lambda k, d=None: q.get(k, [d])[0]
        if u.path in ("/", "/index.html"):
            return self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html")
        if u.path == "/api/messages":  # vista global (para la web)
            after = int(g("after", 0))
            wait = min(float(g("wait", 0)), 60)
            end = time.time() + wait
            with cond:
                while not [m for m in messages if m["id"] > after] and time.time() < end:
                    cond.wait(timeout=max(0.1, end - time.time()))
                now = time.time()
                return self.send(200, {
                    "messages": [m for m in messages if m["id"] > after],
                    "presence": {p: {"seen_ago": (now - t) if t else None, "waiting": waiting[p] > 0}
                                 for p, t in presence.items()}})
        if u.path == "/api/inbox":  # no leidos de 'who'; wait = long-poll
            who = g("who")
            wait = min(float(g("wait", 0)), 60)
            if who not in PEOPLE:
                return self.send(400, {"error": "who invalido"})
            end = time.time() + wait
            with cond:
                presence[who] = time.time()
                waiting[who] += 1
                cond.notify_all()
                try:
                    while not unread_for(who) and time.time() < end:
                        cond.wait(timeout=max(0.1, end - time.time()))
                        presence[who] = time.time()
                finally:
                    waiting[who] -= 1
                    cond.notify_all()
                out = unread_for(who)
                if out and g("peek") != "1":
                    cursors[who] = out[-1]["id"]
                    save_state()
                return self.send(200, {"messages": out})
        self.send(404, {"error": "no existe"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/messages":
            return self.send(404, {"error": "no existe"})
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        frm, to, text = d.get("from"), d.get("to", "all"), (d.get("text") or "").strip()
        if frm not in PEOPLE or not text:
            return self.send(400, {"error": "from invalido o texto vacio"})
        with cond:
            m = {"id": (messages[-1]["id"] + 1) if messages else 1, "ts": time.time(),
                 "from": frm, "to": to, "text": text}
            messages.append(m)
            presence[frm] = time.time()
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            cond.notify_all()
        self.send(200, m)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Chat en http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
