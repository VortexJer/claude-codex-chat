"""Chat compartido Claude <-> Codex <-> Usuario (v2). Solo libreria estandar.
Uso: python server.py [puerto]   (por defecto 8777)
Novedades v2: tipos y respuestas, limites de salida, decisiones, acks, documentos por seccion con propietario."""
import re, json, os, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("CHAT_DATA", HERE)  # carpeta de datos (los tests usan una temporal)
LOG = os.path.join(DATA, "messages.jsonl")
STATE = os.path.join(DATA, "state.json")
DECISIONS = os.path.join(DATA, "decisions.json")
DOCDIR = os.path.join(DATA, "docs")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
PEOPLE = ("claude", "codex", "user")
TYPES = ("msg", "proposal", "objection", "decision", "question", "data", "info", "review", "ack")
DSTATUS = ("proposed", "accepted", "rejected", "superseded")

cond = threading.Condition()
messages = []
cursors = {p: 0 for p in PEOPLE}   # ultimo id ENTREGADO
acks = {p: 0 for p in PEOPLE}      # ultimo id CONFIRMADO explicitamente
presence = {p: 0.0 for p in PEOPLE}
waiting = {p: 0 for p in PEOPLE}
decisions = {}                      # id(str) -> {status, by, who, ts}
docs = {}                           # nombre -> doc
deferred = {p: [] for p in PEOPLE}  # ids que cada uno dejo "en pendiente" a proposito
usage_snap = {p: None for p in PEOPLE}   # ultimo uso visto cuando cada IA envio un mensaje
usage = {p: None for p in PEOPLE}   # {"pct": 0-100 uso RESTANTE, "resets": "21:00", "note": "...", "ts": epoch}
away = {p: None for p in PEOPLE}    # {"until": "21:00", "note": "..."} cuando alguien no esta disponible (p. ej. sin uso)

os.makedirs(DOCDIR, exist_ok=True)
if os.path.exists(LOG):
    with open(LOG, encoding="utf-8") as f:
        messages = [json.loads(l) for l in f if l.strip()]
if os.path.exists(STATE):
    try:
        d = json.load(open(STATE, encoding="utf-8"))
        if "cursors" in d:                # formato v2 inicial (migracion)
            cursors.update(d["cursors"]); acks.update(d.get("acks", {}))
        acks.update(d.pop("_acks", {}))   # v2 actual: cursores PLANOS + "_acks" (rollback a v1 seguro)
        for p, ids in (d.pop("_deferred", {}) or {}).items():
            if p in deferred and isinstance(ids, list): deferred[p] = ids
        for p, a in (d.pop("_away", {}) or {}).items():
            if p in away and (a is None or isinstance(a, dict)): away[p] = a
        for p, u_ in (d.pop("_snap", {}) or {}).items():
            if p in usage_snap and (u_ is None or isinstance(u_, dict)): usage_snap[p] = u_
        for p, u_ in (d.pop("_usage", {}) or {}).items():
            if p in usage and (u_ is None or isinstance(u_, dict)): usage[p] = u_
        cursors.update({k: v for k, v in d.items() if k in PEOPLE})
    except Exception:
        pass
if os.path.exists(DECISIONS):
    try: decisions = json.load(open(DECISIONS, encoding="utf-8"))
    except Exception: pass
for fn in os.listdir(DOCDIR):
    if fn.endswith(".json"):
        docs[fn[:-5]] = json.load(open(os.path.join(DOCDIR, fn), encoding="utf-8"))


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_state():
    atomic_write(STATE, json.dumps({**cursors, "_acks": acks, "_deferred": deferred, "_away": away, "_usage": usage, "_snap": usage_snap}))


def save_decisions():
    atomic_write(DECISIONS, json.dumps(decisions, ensure_ascii=False))


def num(v, default, lo, hi, as_int=False):
    """Numero de la query; None si es invalido (NaN, inf, texto). Se recorta a [lo, hi]."""
    if v is None:
        return default
    try:
        x = float(v)
    except ValueError:
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    x = max(lo, min(hi, x))
    return int(x) if as_int else x


def save_doc(name):
    d = docs[name]
    atomic_write(os.path.join(DOCDIR, name + ".json"), json.dumps(d, ensure_ascii=False))
    md = [f"# {name} (v{d['version']})\n"]
    for k, s in d["sections"].items():
        md.append(f"## {s['title']}  <!-- {k} | propietario: {s['owner']} | v{s['version']} -->\n{s['text'] or '(vacio)'}\n")
    atomic_write(os.path.join(DOCDIR, name + ".md"), "\n".join(md))


# NOTA: "from" es AUTODECLARADO (no hay autenticacion). El propietario de una seccion es un
# control de coordinacion para no pisarse, NO una medida de seguridad.
def shape(m, max_chars):
    m = dict(m)
    m["decision"] = decisions.get(str(m["id"]), {}).get("status")
    if max_chars and len(m["text"]) > max_chars:
        m["trunc"] = len(m["text"]) - max_chars
        m["text"] = m["text"][:max_chars]
    return m


def unread_for(who):
    return [m for m in messages if m["id"] > cursors[who] and m["from"] != who
            and m["to"] in (who, "all")]



# ---------- archivos enlazables (/f/<ruta>) ----------
FILE_TYPES = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8",
              ".txt": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8", ".json": "text/plain; charset=utf-8",
              ".py": "text/plain; charset=utf-8", ".js": "text/plain; charset=utf-8", ".css": "text/plain; charset=utf-8",
              ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
              ".pdf": "application/pdf", ".woff2": "font/woff2"}
BLOCKED_NAMES = {"state.json", "decisions.json", "messages.jsonl", "messages.jsonl.tmp", "state.json.tmp"}
BLOCKED_DIRS = {"docs", "__pycache__", ".git", ".tmp", "node_modules"}


def resolve_file(rel):
    """Ruta relativa (o absoluta dentro del proyecto) -> ruta real permitida, o None. Sin salir de HERE."""
    rel = (rel or "").replace("\\", "/").strip()
    root = os.path.realpath(HERE)
    if len(rel) > 2 and rel[1] == ":" and rel[0].isalpha():          # ruta absoluta de Windows dentro del proyecto
        r = os.path.realpath(rel)
        if not (r + os.sep).startswith(root + os.sep): return None
        rel = os.path.relpath(r, root).replace("\\", "/")
    rel = rel.lstrip("/")
    if not rel or ".." in rel.split("/") or "\x00" in rel: return None
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.isdir(full): full = os.path.join(full, "index.html")
    if not full.startswith(root + os.sep) or not os.path.isfile(full): 
        full = None
        if "/" not in rel:                                           # solo el nombre: buscar en la raiz y en work/ (poca profundidad)
            for base, dirs, files in os.walk(root):
                depth = os.path.relpath(base, root).count(os.sep)
                dirs[:] = [d for d in dirs if d not in BLOCKED_DIRS and depth < 3]
                if rel in files: full = os.path.join(base, rel); break
        if not full: return None
    parts = os.path.relpath(full, root).replace("\\", "/").split("/")
    ext = os.path.splitext(full)[1].lower()
    if ext not in FILE_TYPES or parts[-1] in BLOCKED_NAMES or any(x in BLOCKED_DIRS for x in parts[:-1]):
        return None
    return full


# ---------- uso restante automatico ----------
CODEX_SESSIONS = os.environ.get("CHAT_CODEX_SESSIONS", os.path.join(os.path.expanduser("~"), ".codex", "sessions"))
CLAUDE_USAGE = os.environ.get("CHAT_CLAUDE_USAGE", os.path.join(DATA, "claude_usage.json"))
_RL = re.compile(r'"rate_limits":\{"limit_id":"codex".*?"primary":\{"used_percent":([\d.]+),"window_minutes":(\d+),"resets_at":(\d+)\}'
                 r'(?:,"secondary":\{"used_percent":([\d.]+),"window_minutes":(\d+),"resets_at":(\d+)\})?', re.S)


def _hhmm(epoch, with_date=False):
    t = time.localtime(epoch)
    return time.strftime("%d/%m %H:%M" if with_date else "%H:%M", t)


def _mk_usage(used5, reset5, usedw, resetw, note, ts):
    now = time.time()
    pct = 100.0 - float(used5)
    note_ = note
    if reset5 and reset5 < now:                       # la ventana ya se reinicio desde ese dato
        pct, note_ = 100.0, note + " (ventana reiniciada desde el ultimo dato)"
    out = {"pct": round(max(0.0, min(100.0, pct)), 1), "resets": _hhmm(reset5) if reset5 else "", "note": note_, "ts": ts, "by": "auto"}
    if usedw is not None:
        wp = 100.0 - float(usedw)
        if resetw and resetw < now: wp = 100.0
        out["week_pct"] = round(max(0.0, min(100.0, wp)), 1)
        if resetw: out["week_resets"] = _hhmm(resetw, True)
    return out


def _iso_epoch(ts):
    import calendar
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) + (float(ts[19:23]) if ts[19:20] == "." and ts[20:23].isdigit() else 0.0)
    except Exception:
        return None


_CODEX_CACHE = {"key": None, "best": None}   # evita releer MB de registros si no ha cambiado ningun archivo


def read_codex_usage():
    try:
        cands = []
        for base, dirs, files in os.walk(CODEX_SESSIONS):
            for fn in files:
                if fn.endswith(".jsonl"):
                    fp = os.path.join(base, fn); cands.append((os.path.getmtime(fp), fp))
        top = sorted(cands, reverse=True)[:5]
        key = tuple((fp, mt, os.path.getsize(fp)) for mt, fp in top)
        if key == _CODEX_CACHE["key"]:
            best = _CODEX_CACHE["best"]
            if not best: return None
            ev, used5, r5, usedw, rw = best
            return _mk_usage(used5, r5, usedw, rw, "auto: registros de Codex", ev)
        best = None   # (epoch_evento, used5, reset5, usedw, resetw)
        for mt, fp in top:
            with open(fp, "rb") as f:
                f.seek(0, os.SEEK_END); size = f.tell(); f.seek(max(0, size - 700000))
                tail = f.read().decode("utf-8", "ignore")
            for line in reversed(tail.splitlines()):
                if '"rate_limits":{"limit_id":"codex"' not in line: continue
                try:
                    o = json.loads(line)
                    rl = (o.get("payload") or {}).get("rate_limits") or {}
                    pr, se = rl.get("primary") or {}, rl.get("secondary") or {}
                    if pr.get("used_percent") is None: continue
                    ev = _iso_epoch(o.get("timestamp", "")) or mt
                    if not best or ev > best[0]:
                        best = (ev, pr["used_percent"], pr.get("resets_at"), se.get("used_percent"), se.get("resets_at"))
                    break
                except Exception:
                    continue
        _CODEX_CACHE.update(key=key, best=best)
        if not best: return None
        ev, used5, r5, usedw, rw = best
        return _mk_usage(used5, r5, usedw, rw, "auto: registros de Codex", ev)
    except Exception:
        return None


def read_claude_usage():
    try:
        d = json.load(open(CLAUDE_USAGE, encoding="utf-8"))
        five, seven = d.get("five_hour") or {}, d.get("seven_day") or {}
        if five.get("used_percentage") is None: return None
        r5, rw = five.get("resets_at"), seven.get("resets_at")
        return _mk_usage(five["used_percentage"], float(r5) if r5 else None, seven.get("used_percentage"),
                         float(rw) if rw else None, "auto: barra de estado de Claude Code", d.get("ts") or os.path.getmtime(CLAUDE_USAGE))
    except Exception:
        return None


def refresh_usage():
    changed = False
    with cond:
        for who, fn in (("claude", read_claude_usage), ("codex", read_codex_usage)):
            new = fn()
            if not new: continue
            cur = usage.get(who)
            if cur and cur.get("by") != "auto" and cur.get("ts", 0) > new["ts"]: continue   # un dato manual mas reciente manda
            if not cur or {k: v for k, v in cur.items() if k != "ts"} != {k: v for k, v in new.items() if k != "ts"} or abs(cur.get("ts", 0) - new["ts"]) > 600:
                usage[who] = new; changed = True
        if changed:
            save_state(); cond.notify_all()


def usage_loop():
    every = float(os.environ.get("CHAT_USAGE_POLL", "20"))
    while True:
        try: refresh_usage()
        except Exception: pass
        time.sleep(every)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # la API nunca se cachea (el sondeo largo repite URL)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        g = lambda k, d=None: q.get(k, [d])[0]
        if u.path in ("/", "/index.html"):
            return self.send(200, open(os.path.join(HERE, "index.html"), "rb").read().replace(b"__PROJECT_ROOT__", (HERE.replace(chr(92), "/").lower() + "/").encode()), "text/html")
        if u.path == "/api/messages":  # vista global (para la web y log)
            after, wait = num(g("after"), 0, 0, 1e12, True), num(g("wait"), 0, 0, 60)
            limit, max_chars = num(g("limit"), 0, 0, 10000, True), num(g("max_chars"), 0, 0, 1e7, True)
            if None in (after, wait, limit, max_chars):
                return self.send(400, {"error": "parametro numerico invalido"})
            end = time.time() + wait
            with cond:
                while not [m for m in messages if m["id"] > after] and time.time() < end:
                    cond.wait(timeout=max(0.1, end - time.time()))
                now = time.time()
                out = [m for m in messages if m["id"] > after]
                if limit: out = out[-limit:] if g("tail") == "1" else out[:limit]
                return self.send(200, {
                    "messages": [shape(m, max_chars) for m in out],
                    "presence": {p: {"seen_ago": (now - t) if t else None, "waiting": waiting[p] > 0}
                                 for p, t in presence.items()},
                    "delivered": dict(cursors), "confirmed": dict(acks),
                    "unread": {p: [m["id"] for m in unread_for(p)] for p in PEOPLE},
                    "deferred": {p: list(v) for p, v in deferred.items()},
                    "away": {p: a for p, a in away.items() if a},
                    "usage": {p: u_ for p, u_ in usage.items() if u_}})
        if u.path == "/api/message":
            with cond:
                for m in messages:
                    if str(m["id"]) == g("id"):
                        return self.send(200, shape(m, 0))
            return self.send(404, {"error": "mensaje no existe"})
        if u.path == "/api/inbox":  # no leidos de 'who'; wait = long-poll
            who = g("who"); wait = num(g("wait"), 0, 0, 60)
            limit, max_chars = num(g("limit"), 0, 0, 10000, True), num(g("max_chars"), 0, 0, 1e7, True)
            if who not in PEOPLE:
                return self.send(400, {"error": "who invalido"})
            if None in (wait, limit, max_chars):
                return self.send(400, {"error": "parametro numerico invalido"})
            end = time.time() + wait
            with cond:
                presence[who] = time.time(); waiting[who] += 1; cond.notify_all()
                try:
                    while not unread_for(who) and time.time() < end:
                        cond.wait(timeout=max(0.1, end - time.time()))
                        presence[who] = time.time()
                finally:
                    waiting[who] -= 1; cond.notify_all()
                allu = unread_for(who)
                out = allu[:limit] if limit else allu
                if out and g("peek") != "1":
                    cursors[who] = out[-1]["id"]  # solo hasta lo entregado: nada se pierde
                    save_state()
                rest = allu[len(out):]
                return self.send(200, {"messages": [shape(m, max_chars) for m in out],
                                       "more": len(rest),
                                       "guide": [{"id": m["id"], "type": m.get("type") or "msg", "from": m["from"]}
                                                 for m in rest[:5]],
                                       "deferred": deferred[who][:10]})
        if u.path.startswith("/f/"):
            from urllib.parse import unquote
            full = resolve_file(unquote(u.path[3:]))
            if not full: return self.send(404, {"error": "archivo no disponible"})
            ext = os.path.splitext(full)[1].lower()
            data = open(full, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", FILE_TYPES[ext]); self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")          # fuentes e imagenes de paginas en sandbox
            if ext in (".html", ".htm", ".svg"):
                self.send_header("Content-Security-Policy", "sandbox allow-scripts")   # sin acceso a la API del chat
            self.end_headers(); self.wfile.write(data)
            return
        if u.path.startswith("/assets/"):
            # solo ficheros de la carpeta assets/ con extension permitida; sin salir de ella
            import re as _re
            name = u.path[len("/assets/"):]
            types = {".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".webp": "image/webp", ".txt": "text/plain"}
            root = os.path.realpath(os.path.join(HERE, "assets"))
            full = os.path.realpath(os.path.join(root, name))
            ext = os.path.splitext(name)[1].lower()
            if (not _re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?", name) or ".." in name
                    or ext not in types or not full.startswith(root + os.sep) or not os.path.isfile(full)):
                return self.send(404, {"error": "no existe"})
            data = open(full, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", types[ext])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/brief":
            who = g("who")
            if who not in PEOPLE:
                return self.send(400, {"error": "who invalido"})
            with cond:
                unread = unread_for(who)
                last_by = {}
                for m in messages:
                    last_by[m["from"]] = m["id"]
                open_items = [{"id": m["id"], "type": m.get("type") or "msg", "from": m["from"],
                               "head": m["text"][:90].replace("\n", " ")}
                              for m in messages if (m.get("type") in ("objection", "question")
                                                    and m["from"] != who
                                                    and not any(r.get("reply_to") == m["id"] for r in messages))][-5:]
                return self.send(200, {
                    "last_id": messages[-1]["id"] if messages else 0,
                    "unread": [{"id": m["id"], "type": m.get("type") or "msg", "from": m["from"],
                                "head": m["text"][:90].replace("\n", " ")} for m in unread[:8]],
                    "unread_count": len(unread),
                    "decisions": {k: v["status"] for k, v in decisions.items() if v["status"] in ("accepted", "proposed")},
                    "deferred": deferred[who][:10], "open": open_items,
                    "away": {p: a for p, a in away.items() if a}, "usage": {p: u_ for p, u_ in usage.items() if u_},
                    "last_by": last_by})
        if u.path == "/api/decisions":
            with cond:
                st = g("status")
                return self.send(200, {"decisions": {k: v for k, v in decisions.items()
                                                      if not st or v["status"] == st}})
        if u.path == "/api/docs":
            with cond:
                return self.send(200, {"docs": {n: {"version": d["version"],
                                                    "sections": {k: {"owner": s["owner"], "version": s["version"]}
                                                                 for k, s in d["sections"].items()}}
                                                for n, d in docs.items()}})
        if u.path == "/api/doc":
            with cond:
                d = docs.get(g("name"))
                if not d: return self.send(404, {"error": "doc no existe"})
                if g("section"):
                    s = d["sections"].get(g("section"))
                    if not s: return self.send(404, {"error": "seccion no existe"})
                    if g("history") != "1": s = {k: v for k, v in s.items() if k != "history"}
                    return self.send(200, s)
                return self.send(200, {"name": g("name"), "version": d["version"], "sections": {
                    k: {kk: vv for kk, vv in s.items() if kk != "history"} for k, s in d["sections"].items()}})
        self.send(404, {"error": "no existe"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return self.send(400, {"error": "json invalido"})
        if not isinstance(d, dict):
            return self.send(400, {"error": "se esperaba un objeto json"})
        frm = d.get("from")
        if frm not in PEOPLE:
            return self.send(400, {"error": "from invalido"})
        with cond:
            if path == "/api/messages":
                to = d.get("to", "all")
                text = d.get("text").strip() if isinstance(d.get("text"), str) else ""
                typ = d.get("type") or "msg"
                re_ = d.get("reply_to")
                if not isinstance(d.get("text"), str) or not text:
                    return self.send(400, {"error": "texto vacio o no es texto"})
                if to not in PEOPLE + ("all",): return self.send(400, {"error": "to invalido"})
                if typ not in TYPES: return self.send(400, {"error": f"type invalido, usa {TYPES}"})
                if re_ is not None and not any(m["id"] == re_ for m in messages):
                    return self.send(400, {"error": "reply_to no existe"})
                m = {"id": (messages[-1]["id"] + 1) if messages else 1, "ts": time.time(),
                     "from": frm, "to": to, "text": text, "type": typ, "reply_to": re_}
                if frm in ("claude", "codex"):
                    try:
                        refresh_usage()
                        u_ = usage.get(frm)
                        if u_:
                            prev = usage_snap.get(frm)
                            spent = None
                            if prev and prev.get("resets") == u_.get("resets") and prev.get("pct", 0) >= u_["pct"]:
                                spent = round(prev["pct"] - u_["pct"], 1)
                            m["use"] = {"left": u_["pct"], "spent": spent, "week": u_.get("week_pct"),
                                        "auto": u_.get("by") == "auto", "age": int(max(0, time.time() - u_.get("ts", time.time())) // 60)}
                            usage_snap[frm] = {"pct": u_["pct"], "resets": u_.get("resets"), "ts": time.time()}
                            save_state()
                    except Exception:
                        pass
                messages.append(m); presence[frm] = time.time()
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
                cond.notify_all()
                return self.send(200, m)
            if path == "/api/decide":
                mid, st = str(d.get("id")), d.get("status")
                if not mid.isdigit(): return self.send(400, {"error": "id invalido"})
                if st not in DSTATUS: return self.send(400, {"error": f"status invalido, usa {DSTATUS}"})
                if not any(str(m["id"]) == mid for m in messages):
                    return self.send(404, {"error": "mensaje no existe"})
                decisions[mid] = {"status": st, "by": d.get("by"), "who": frm, "ts": time.time()}
                save_decisions(); cond.notify_all()
                return self.send(200, decisions[mid])
            if path in ("/api/defer", "/api/done"):
                mid = d.get("id")
                if not isinstance(mid, int) or not any(m["id"] == mid for m in messages):
                    return self.send(400, {"error": "id invalido o inexistente"})
                if path == "/api/defer":
                    if mid not in deferred[frm]: deferred[frm].append(mid)
                else:
                    if mid in deferred[frm]: deferred[frm].remove(mid)
                save_state()
                return self.send(200, {"deferred": deferred[frm]})
            if path == "/api/away":
                who = d.get("who", frm)
                if who not in PEOPLE: return self.send(400, {"error": "who invalido"})
                if d.get("clear"):
                    away[who] = None
                else:
                    until, note = d.get("until"), d.get("note")
                    if (until is not None and not isinstance(until, str)) or (note is not None and not isinstance(note, str)):
                        return self.send(400, {"error": "until y note deben ser texto"})
                    away[who] = {"until": (until or "")[:40], "note": (note or "")[:200], "ts": time.time()}
                save_state(); cond.notify_all()
                return self.send(200, {"away": away[who]})
            if path == "/api/usage":
                who = d.get("who", frm)
                if who not in PEOPLE or who == "user": return self.send(400, {"error": "who invalido"})
                if d.get("clear"):
                    usage[who] = None
                else:
                    pct, resets, note = d.get("pct"), d.get("resets"), d.get("note")
                    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not (0 <= pct <= 100):
                        return self.send(400, {"error": "pct debe ser un numero entre 0 y 100 (uso restante)"})
                    if (resets is not None and not isinstance(resets, str)) or (note is not None and not isinstance(note, str)):
                        return self.send(400, {"error": "resets y note deben ser texto"})
                    usage[who] = {"pct": round(float(pct), 1), "resets": (resets or "")[:40], "note": (note or "")[:200],
                                  "ts": time.time(), "by": frm}
                save_state(); cond.notify_all()
                return self.send(200, {"usage": usage[who]})
            if path == "/api/ack":
                mid = d.get("id")
                if not isinstance(mid, int) or mid < 0: return self.send(400, {"error": "id invalido"})
                if mid > (messages[-1]["id"] if messages else 0): return self.send(400, {"error": "id futuro"})
                acks[frm] = max(acks[frm], mid); save_state(); cond.notify_all()
                return self.send(200, {"confirmed": acks[frm]})
            if path == "/api/doc/init":
                name, secs = d.get("name"), d.get("sections", {})
                if not isinstance(secs, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in secs.items()):
                    return self.send(400, {"error": "sections debe ser {clave: {owner,title}}"})
                if not isinstance(name, str): return self.send(400, {"error": "nombre invalido"})
                if not name or not name.replace("_", "").replace("-", "").isalnum():
                    return self.send(400, {"error": "nombre invalido"})
                if name in docs: return self.send(409, {"error": "ya existe"})
                for k, s in secs.items():
                    if s.get("owner") not in PEOPLE: return self.send(400, {"error": f"owner invalido en {k}"})
                docs[name] = {"version": 1, "sections": {k: {"title": s.get("title", k), "owner": s["owner"],
                              "version": 0, "text": "", "history": []} for k, s in secs.items()}}
                save_doc(name)
                return self.send(200, {"name": name, "version": 1})
            if path == "/api/doc":
                name, k = d.get("name"), d.get("section")
                doc = docs.get(name)
                if not doc or k not in doc["sections"]: return self.send(404, {"error": "doc o seccion no existe"})
                s = doc["sections"][k]
                if frm != s["owner"]:
                    return self.send(403, {"error": f"solo {s['owner']} puede escribir la seccion {k}"})
                if not isinstance(d.get("expected_version"), int):
                    return self.send(400, {"error": "falta expected_version (entero)"})
                if d.get("expected_version") != s["version"]:
                    return self.send(409, {"error": "version desfasada", "current": s["version"]})
                text = d.get("text")
                if not isinstance(text, str): return self.send(400, {"error": "falta text"})
                s["history"].append({"v": s["version"], "author": frm, "ts": time.time(), "text": s["text"]})
                s["version"] += 1; s["text"] = text; doc["version"] += 1
                save_doc(name); cond.notify_all()
                return self.send(200, {"section": k, "version": s["version"], "doc_version": doc["version"]})
        self.send(404, {"error": "no existe"})


if __name__ == "__main__":
    threading.Thread(target=usage_loop, daemon=True).start()   # uso restante automatico (Codex: registros; Claude: barra de estado)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Chat v2 en http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
