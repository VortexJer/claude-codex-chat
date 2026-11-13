"""Cliente CLI del chat (v2). Solo libreria estandar. Servidor: env CHAT_URL (def. http://127.0.0.1:8777)
FORMAS VIEJAS (siguen igual):
  chat.py say  <yo> "texto" [--to X] [--type T] [--re ID]     (texto '-' = stdin)
  chat.py wait <yo> [--timeout 300] [--limit N] [--max-chars M]   (codigo 2 = timeout, 3 = servidor caido)
  chat.py read <yo> [--peek] [--limit N] [--max-chars M]
  chat.py log [--last N] [--since ID] [--brief]    chat.py status
NUEVO (usa --as <yo>):
  chat.py get ID                                    mensaje completo (cuando salio truncado)
  chat.py decide ID proposed|accepted|rejected|superseded [--by ID] --as <yo>
  chat.py decisions [--status S]
  chat.py ack ID --as <yo>                          confirma lectura/aceptacion
  chat.py brief --as <yo>    PUESTA AL DIA barata (sin cuerpos): sin leer, decisiones, pendientes, preguntas abiertas
  chat.py away <quien> [--until 21:00] [--note "sin uso"] | away <quien> --clear     marca no disponible
  chat.py usage <quien> <pct 0-100> [--until 21:00] [--note ..]   apunta el USO RESTANTE (%) de claude o codex; usage <quien> --clear; usage (sin args) lo muestra
  chat.py defer ID --as <yo>   deja ese mensaje EN PENDIENTE (solo si es imprescindible);
  chat.py pending --as <yo>    lista tus pendientes (una linea cada uno);  chat.py done ID --as <yo>  lo cierra
  read/wait entregan 1 mensaje por llamada (--limit N para mas, --limit 0 para todos) y una guia de lo que queda.
  A los mensajes de tipo info/ack y a los del usuario NO se responde salvo que sean una pregunta.
  chat.py doc ls | doc get NAME [SECCION] | doc init NAME k:owner:Titulo ... --as <yo>
  chat.py doc set NAME SECCION --version V "texto"|- --as <yo>     (solo el propietario; 409 si V no coincide)
Tipos: msg proposal objection decision question data info review ack"""
import json, os, sys, time, argparse, urllib.request, urllib.error

URL = os.environ.get("CHAT_URL", "http://127.0.0.1:8777")
OTHER = {"claude": "codex", "codex": "claude", "user": "all"}


class HttpErr(Exception):
    def __init__(self, code, body):
        self.code, self.body = code, body


def http(path, data=None, timeout=70):
    req = urllib.request.Request(
        URL + path, method="POST" if data is not None else "GET",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: body = json.loads(e.read())
        except Exception: body = {}
        raise HttpErr(e.code, body)


def show(ms, brief=False):
    for m in ms:
        t = time.strftime("%H:%M:%S", time.localtime(m["ts"]))
        typ = m.get("type") or "msg"
        re_ = f" re#{m['reply_to']}" if m.get("reply_to") else ""
        dec = f" [{m['decision']}]" if m.get("decision") else ""
        if brief:
            one = m["text"].replace("\n", " ")
            print(f"#{m['id']} {m['from']}>{m['to']} {typ}{re_}{dec}: {one[:110]}{'...' if len(one) > 110 else ''}")
            continue
        tr = f"\n[+{m['trunc']} car. omitidos; usar: chat.py get {m['id']}]" if m.get("trunc") else ""
        print(f"[#{m['id']} {t} {m['from']} -> {m['to']} {typ}{re_}{dec}]\n{m['text']}{tr}\n")


def usage_lines(us, me, always=False):
    """Lineas de 'uso restante' de cada IA; avisa si alguna esta por debajo del 15 %."""
    out, low = [], []
    for p in ("claude", "codex"):
        v = us.get(p)
        if not v:
            out.append(f"uso restante {p}: sin dato"); continue
        ago = max(0, int(time.time() - v.get("ts", time.time())))
        age = f"hace {ago // 3600} h" if ago >= 3600 else f"hace {ago // 60} min"
        out.append(f"uso restante {p}: {v['pct']:g} %" + (f" (reinicia {v['resets']})" if v.get("resets") else "") + f" [{age}]" + (f" {v['note']}" if v.get("note") else ""))
        if v["pct"] <= 15: low.append(p)
    for p in low:
        who = "TU" if p == me else p.upper()
        out.append(f"AVISO: {who} al limite de uso: mensajes cortos, entrega lo esencial y deja el relevo escrito (work/ESTADO.md)")
    return out if (always or us) else []


def main():
    for s in (sys.stdout, sys.stdin):
        try: s.reconfigure(encoding="utf-8")
        except Exception: pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--to"); ap.add_argument("--type"); ap.add_argument("--re", type=int)
    ap.add_argument("--as", dest="me"); ap.add_argument("--by", type=int)
    ap.add_argument("--status"); ap.add_argument("--version", type=int)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--last", type=int, default=0); ap.add_argument("--since", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None); ap.add_argument("--max-chars", type=int, default=None)
    ap.add_argument("--until"); ap.add_argument("--note"); ap.add_argument("--clear", action="store_true")
    ap.add_argument("--peek", action="store_true"); ap.add_argument("--brief", action="store_true")
    a = ap.parse_intermixed_args()
    r, c = a.rest, a.cmd
    me = a.me or (r[0] if r and r[0] in OTHER else None)
    text_of = lambda x: sys.stdin.read() if x == "-" else x
    try:
        if c == "say":
            m = http("/api/messages", {"from": r[0], "to": a.to or OTHER.get(r[0], "all"),
                                       "text": text_of(r[1]), "type": a.type, "reply_to": a.re})
            print(f"enviado #{m['id']}")
            n = len(text_of(r[1]))
            if n > 900: print(f"AVISO: mensaje de {n} car. La otra IA paga cada caracter: deja <=600 y enlaza el detalle en un archivo (work/...md) para que se abra con un clic")
        elif c in ("wait", "read"):
            end = time.time() + (a.timeout if c == "wait" else 0)
            lim = 1 if a.limit is None else a.limit   # por defecto un mensaje por llamada
            mc = 1400 if a.max_chars is None else a.max_chars   # ahorro de tokens: cuerpo recortado a 1400 car. (--max-chars 0 = completo, o chat.py get ID)
            extra = ("&peek=1" if a.peek else "") + f"&limit={lim}&max_chars={mc}"
            while True:
                left = max(0, end - time.time())
                resp = http(f"/api/inbox?who={r[0]}&wait={min(left, 25)}{extra}")
                if resp["messages"]:
                    show(resp["messages"])
                    if resp.get("more"):
                        g = ", ".join(f"#{x['id']}({x['type']},{x['from']})" for x in resp["guide"])
                        print(f"[quedan {resp['more']} sin leer: {g}{' ...' if resp['more'] > len(resp['guide']) else ''}; vuelve a llamar]")
                    if resp.get("deferred"):
                        print(f"[en pendiente: {', '.join('#' + str(i) for i in resp['deferred'])}; ver con: pending --as {r[0]}]")
                    return 0
                if time.time() >= end:
                    if c == "wait":
                        print("TIMEOUT: sin mensajes nuevos"); return 2
                    print("sin mensajes nuevos"); return 0
        elif c == "log":
            q = f"/api/messages?after={a.since}&limit={a.last or 0}&tail=1&max_chars={a.max_chars or (110 if a.brief else 0)}"
            show(http(q)["messages"], a.brief)
        elif c == "get":
            show([http(f"/api/message?id={r[0]}")])
        elif c == "status":
            d = http("/api/messages?after=999999999")
            for p, v in d["presence"].items():
                if p == "user": continue
                s = "nunca visto" if v["seen_ago"] is None else f"visto hace {int(v['seen_ago'])}s"
                print(f"{p}: {s}{' (ESPERANDO)' if v['waiting'] else ''} | entregado hasta #{d['delivered'][p]}, confirmado hasta #{d['confirmed'][p]} | sin leer: {d['unread'][p] or 'ninguno'}")
        elif c == "decide":
            print(http("/api/decide", {"from": a.me, "id": int(r[0]), "status": r[1], "by": a.by}))
        elif c == "decisions":
            for k, v in http("/api/decisions" + (f"?status={a.status}" if a.status else ""))["decisions"].items():
                print(f"#{k}: {v['status']} (por {v['who']}{', sustituida por #' + str(v['by']) if v.get('by') else ''})")
        elif c == "brief":
            b = http(f"/api/brief?who={a.me or (r[0] if r else '')}")
            print(f"ultimo id #{b['last_id']} | sin leer: {b['unread_count']}")
            for x in b["unread"]:
                print(f"  #{x['id']} {x['type']} de {x['from']}: {x['head']}")
            if b["decisions"]:
                print("decisiones: " + ", ".join(f"#{k}={v}" for k, v in b["decisions"].items()))
            if b["deferred"]:
                print("en pendiente: " + ", ".join(f"#{i}" for i in b["deferred"]))
            for x in b["open"]:
                print(f"abierto sin respuesta: #{x['id']} {x['type']} de {x['from']}: {x['head']}")
            for p, v in b["away"].items():
                print(f"{p} no disponible" + (f" hasta {v['until']}" if v.get("until") else "") + (f" ({v['note']})" if v.get("note") else ""))
            for line in usage_lines(b.get("usage", {}), a.me or (r[0] if r else "")):
                print(line)
            print("para leer el contenido: chat.py read <yo> (uno por llamada) o chat.py get ID")
        elif c == "usage":
            if not r:
                for line in usage_lines(http("/api/messages?after=999999999").get("usage", {}), a.me or "", always=True): print(line)
            else:
                who = r[0]
                body = {"from": a.me or who, "who": who}
                if a.clear: body["clear"] = True
                else:
                    if len(r) < 2: sys.exit("uso: chat.py usage <quien> <pct 0-100> [--until HH:MM] [--note ..]")
                    body.update({"pct": float(r[1].rstrip("%").replace(",", ".")), "resets": a.until, "note": a.note})
                print(http("/api/usage", body))
        elif c == "away":
            who = r[0] if r else a.me
            body = {"from": a.me or who, "who": who}
            if a.clear: body["clear"] = True
            else: body.update({"until": a.until, "note": a.note})
            print(http("/api/away", body))
        elif c in ("defer", "done"):
            print(http(f"/api/{c}", {"from": a.me, "id": int(r[0])}))
        elif c == "pending":
            ids = http("/api/messages?after=999999999")["deferred"].get(a.me, [])
            for i in ids:
                show([http(f"/api/message?id={i}")], True)
            if not ids: print("sin pendientes")
        elif c == "ack":
            print(http("/api/ack", {"from": a.me, "id": int(r[0])}))
        elif c == "doc":
            sub = r[0]
            if sub == "ls":
                for n, d in http("/api/docs")["docs"].items():
                    print(f"{n} v{d['version']}: " + ", ".join(f"{k}(v{s['version']},{s['owner']})" for k, s in d["sections"].items()))
            elif sub == "get":
                if len(r) > 2:
                    s = http(f"/api/doc?name={r[1]}&section={r[2]}")
                    print(f"## {s['title']} [v{s['version']}, {s['owner']}]\n{s['text']}")
                else:
                    d = http(f"/api/doc?name={r[1]}")
                    for k, s in d["sections"].items():
                        print(f"## {k}: {s['title']} [v{s['version']}, {s['owner']}]\n{s['text']}\n")
            elif sub == "init":
                secs = {}
                for sp in r[2:]:
                    k, o, *t = sp.split(":", 2)
                    secs[k] = {"owner": o, "title": t[0] if t else k}
                print(http("/api/doc/init", {"from": a.me, "name": r[1], "sections": secs}))
            elif sub == "set":
                print(http("/api/doc", {"from": a.me, "name": r[1], "section": r[2], "text": text_of(r[3]),
                                         "expected_version": a.version}))
            else:
                print(__doc__); return 1
        else:
            print(__doc__); return 0 if c == "help" else 1
    except HttpErr as e:
        print(f"ERROR {e.code}: {e.body.get('error', e.body)}" + (f" (version actual: {e.body['current']})" if "current" in e.body else ""))
        return 4
    except (IndexError, ValueError, TypeError):
        print("ERROR: argumentos incompletos o invalidos. Ejecuta: python chat.py help"); return 1
    except urllib.error.URLError as e:
        print(f"ERROR: servidor no responde en {URL} ({e}). Arranca: python server.py")
        return 3
    return 0


sys.exit(main())
