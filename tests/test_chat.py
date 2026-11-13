"""Pruebas del chat v2. Arranca un servidor temporal (datos aislados, puerto libre).
Ejecutar:  python -m unittest discover -s tests -v   (desde la carpeta del chat)"""
import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.port = free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        env = dict(os.environ, CHAT_DATA=cls.tmp)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), str(cls.port)],
                                    env=env, stdout=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(cls.url + "/api/decisions", timeout=1); break
            except Exception: time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, data=None):
        r = urllib.request.Request(self.url + path, method="POST" if data is not None else "GET",
                                   data=json.dumps(data).encode() if data is not None else None,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp: return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def say(self, frm, text, to="all", **kw):
        return self.req("/api/messages", dict(**{"from": frm, "to": to, "text": text}, **kw))[1]

    def cli(self, *args, stdin=None):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "chat.py"), *args], capture_output=True,
                           input=stdin, text=True, encoding="utf-8", env=dict(os.environ, CHAT_URL=self.url))
        return p.returncode, p.stdout


class Messages(Base):
    def test_a_no_loss_with_truncation_and_limit(self):
        ids = [self.say("claude", "x" * 500 + str(i), to="codex")["id"] for i in range(5)]
        got = []
        for _ in range(10):  # bucle como el de un agente real
            _, r = self.req("/api/inbox?who=codex&limit=2&max_chars=50")
            if not r["messages"]: break
            for m in r["messages"]:
                self.assertEqual(len(m["text"]), 50); self.assertEqual(m["trunc"], 451)
                got.append(m["id"])
        self.assertEqual(got, ids)  # ni salta ni repite
        full = self.req(f"/api/message?id={ids[0]}")[1]
        self.assertEqual(len(full["text"]), 501)  # lo truncado sigue recuperable

    def test_b_type_and_reply_validation(self):
        m = self.say("codex", "hola", to="claude", type="objection")
        self.assertEqual(m["type"], "objection")
        r = self.say("claude", "re", reply_to=m["id"])
        self.assertEqual(r["reply_to"], m["id"])
        self.assertEqual(self.req("/api/messages", {"from": "claude", "text": "x", "type": "raro"})[0], 400)
        self.assertEqual(self.req("/api/messages", {"from": "claude", "text": "x", "reply_to": 99999})[0], 400)
        self.assertEqual(self.req("/api/messages", {"from": "otro", "text": "x"})[0], 400)

    def test_c_old_cli_forms_still_work(self):
        code, out = self.cli("say", "claude", "viejo", "--to", "codex"); self.assertEqual(code, 0)
        code, out = self.cli("read", "codex", "--limit", "0"); self.assertEqual(code, 0); self.assertIn("viejo", out)  # por defecto ahora es 1 mensaje
        code, out = self.cli("wait", "codex", "--timeout", "1"); self.assertEqual(code, 2)
        code, out = self.cli("log", "--last", "2", "--brief"); self.assertIn("claude>", out)

    def test_d_decisions_and_acks(self):
        m = self.say("codex", "propongo X", type="proposal")
        self.assertEqual(self.req("/api/decide", {"from": "claude", "id": m["id"], "status": "accepted"})[0], 200)
        d = self.req("/api/decisions?status=accepted")[1]["decisions"]
        self.assertIn(str(m["id"]), d)
        self.assertEqual(self.req("/api/decide", {"from": "claude", "id": m["id"], "status": "nope"})[0], 400)
        self.assertEqual(self.req("/api/decide", {"from": "claude", "id": 99999, "status": "accepted"})[0], 404)
        self.assertEqual(self.req("/api/ack", {"from": "codex", "id": m["id"]})[0], 200)
        self.assertEqual(self.req("/api/ack", {"from": "codex", "id": 99999})[0], 400)
        st = self.req("/api/messages?after=999999")[1]
        self.assertEqual(st["confirmed"]["codex"], m["id"])


class NoCache(Base):
    def test_api_responses_are_no_store(self):
        for path in ("/api/messages?after=0&limit=1", "/api/decisions", "/api/brief?who=claude", "/api/docs"):
            with urllib.request.urlopen(self.url + path, timeout=5) as r:
                self.assertEqual(r.headers.get("Cache-Control"), "no-store", path)


class Assets(Base):
    def raw(self, path):
        try:
            with urllib.request.urlopen(self.url + path, timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, None, b""

    def test_serves_real_assets_with_safe_headers(self):
        s, ct, body = self.raw("/assets/claude.svg")
        self.assertEqual((s, ct), (200, "image/svg+xml"))
        self.assertIn(b"<svg", body)
        s, ct, body = self.raw("/assets/fonts/inter-latin-wght-normal.woff2")
        self.assertEqual((s, ct, body[:4]), (200, "font/woff2", b"wOF2"))

    def test_blocks_traversal_and_unknown_types(self):
        for bad in ("/assets/../server.py", "/assets/%2e%2e/server.py", "/assets/..%5cserver.py",
                    "/assets/fonts/../../server.py", "/assets/nope.svg", "/assets/fonts/LICENSE",
                    "/assets/", "/assets/claude.svg/../../chat.py", "/assets/a/b/c.svg"):
            self.assertEqual(self.raw(bad)[0], 404, bad)

    def test_logos_have_no_scripts(self):
        for n in ("claude.svg", "codex.svg"):
            body = open(os.path.join(ROOT, "assets", n), "rb").read().lower()
            for bad in (b"<script", b"onload", b"onclick", b"xlink:href", b"foreignobject", b"@import"):
                self.assertNotIn(bad, body, (n, bad))


class Brief(Base):
    def test_brief_and_away(self):
        a = self.say("codex", "dudas sobre el plan", to="claude", type="question")
        self.say("codex", "objecion al plan", to="claude", type="objection")
        self.say("claude", "respuesta", to="codex", type="msg", reply_to=a["id"])
        p = self.say("codex", "propongo X", to="claude", type="proposal")
        self.req("/api/decide", {"from": "claude", "id": p["id"], "status": "accepted"})
        s, b = self.req("/api/brief?who=claude")
        self.assertEqual(s, 200)
        self.assertEqual(b["unread_count"], 3)
        self.assertTrue(all("head" in x and "text" not in x for x in b["unread"]))   # nunca cuerpos completos
        ids = [x["id"] for x in b["open"]]
        self.assertNotIn(a["id"], ids)               # la pregunta ya tiene respuesta
        self.assertEqual(len(ids), 1)                # queda la objecion
        self.assertEqual(b["decisions"][str(p["id"])], "accepted")
        self.assertEqual(self.req("/api/brief?who=nadie")[0], 400)
        # away: se guarda, aparece en brief y en /api/messages, se borra
        self.assertEqual(self.req("/api/away", {"from": "claude", "who": "codex", "until": "21:00", "note": "sin uso"})[0], 200)
        self.assertEqual(self.req("/api/brief?who=claude")[1]["away"]["codex"]["until"], "21:00")
        self.assertIn("codex", self.req("/api/messages?after=999999")[1]["away"])
        self.assertEqual(self.req("/api/away", {"from": "claude", "who": "x"})[0], 400)
        self.assertEqual(self.req("/api/away", {"from": "claude", "who": "codex", "until": 5})[0], 400)
        st = json.load(open(os.path.join(self.tmp, "state.json")))
        self.assertIn("_away", st)
        self.assertEqual(self.req("/api/away", {"from": "claude", "who": "codex", "clear": True})[0], 200)
        self.assertNotIn("codex", self.req("/api/brief?who=claude")[1]["away"])

    def test_brief_cli(self):
        self.say("codex", "hola cuerpo largo " * 30, to="claude", type="data")
        code, out = self.cli("brief", "--as", "claude")
        self.assertEqual(code, 0)
        self.assertIn("sin leer:", out)
        self.assertNotIn("cuerpo largo cuerpo largo cuerpo largo cuerpo largo cuerpo largo cuerpo largo cuerpo largo", out)
        self.assertEqual(self.cli("away", "codex", "--until", "21:00", "--note", "sin uso", "--as", "claude")[0], 0)
        self.assertIn("codex no disponible hasta 21:00", self.cli("brief", "--as", "claude")[1])


class Usage(Base):
    def test_usage_api(self):
        self.assertEqual(self.req("/api/usage", {"from": "user", "who": "codex", "pct": 13, "resets": "21:00"})[0], 200)
        u = self.req("/api/messages?after=999999")[1]["usage"]
        self.assertEqual(u["codex"]["pct"], 13.0); self.assertEqual(u["codex"]["resets"], "21:00")
        self.assertEqual(self.req("/api/brief?who=claude")[1]["usage"]["codex"]["pct"], 13.0)
        for bad in (150, -1, "10", True, None):                      # solo numeros 0-100
            self.assertEqual(self.req("/api/usage", {"from": "claude", "who": "claude", "pct": bad})[0], 400, bad)
        self.assertEqual(self.req("/api/usage", {"from": "claude", "who": "user", "pct": 50})[0], 400)
        self.assertEqual(self.req("/api/usage", {"from": "claude", "who": "x", "pct": 50})[0], 400)
        self.assertEqual(self.req("/api/usage", {"from": "claude", "who": "claude", "pct": 50, "note": 3})[0], 400)
        st = json.load(open(os.path.join(self.tmp, "state.json")))
        self.assertIn("_usage", st)                                  # persiste
        self.assertEqual(self.req("/api/usage", {"from": "claude", "who": "codex", "clear": True})[0], 200)
        self.assertNotIn("codex", self.req("/api/messages?after=999999")[1]["usage"])

    def test_usage_cli_and_warning(self):
        self.assertEqual(self.cli("usage", "codex", "13%", "--until", "21:00", "--as", "user")[0], 0)
        self.assertEqual(self.cli("usage", "claude", "80", "--as", "claude")[0], 0)
        code, out = self.cli("brief", "--as", "claude")
        self.assertIn("uso restante codex: 13 %", out); self.assertIn("reinicia 21:00", out)
        self.assertIn("uso restante claude: 80 %", out)
        self.assertIn("AVISO: CODEX al limite de uso", out)            # <= 15 %
        code, out = self.cli("brief", "--as", "codex")
        self.assertIn("AVISO: TU al limite de uso", out)
        self.assertNotEqual(self.cli("usage", "codex", "abc", "--as", "claude")[0], 0)
        self.assertIn("uso restante claude: 80 %", self.cli("usage")[1])
        self.cli("usage", "codex", "--clear", "--as", "claude")
        self.assertIn("uso restante codex: sin dato", self.cli("usage")[1])


class Files(Base):
    def raw(self, path):
        try:
            with urllib.request.urlopen(self.url + path, timeout=10) as r: return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

    def test_serves_project_files(self):
        s, h, b = self.raw("/f/README.md")
        self.assertEqual(s, 200); self.assertIn("text/plain", h["Content-Type"]); self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        s, h, b = self.raw("/f/work/demo/index.html")
        self.assertEqual(s, 200); self.assertIn("text/html", h["Content-Type"])
        self.assertIn("sandbox", h["Content-Security-Policy"])               # el HTML no puede hablar con la API del chat
        self.assertEqual(self.raw("/f/assets/fonts/inter-latin-wght-normal.woff2")[0], 200)
        self.assertEqual(self.raw("/f/NOTES.md")[0], 200)               # solo el nombre: se busca en work/
        self.assertEqual(self.raw("/f/work/demo/")[0], 200)   # carpeta -> index.html

    def test_blocks_unsafe_paths(self):
        for bad in ("/f/../server.py", "/f/..%2fserver.py", "/f/work/../../server.py", "/f/C:/Windows/win.ini",
                    "/f/state.json", "/f/messages.jsonl", "/f/tests/../state.json", "/f/server.exe", "/f/", "/f/work/nada-existe.md",
                    "/f/%00", "/f/..%5cserver.py"):
            self.assertEqual(self.raw(bad)[0], 404, bad)


class AutoUsage(Base):
    @classmethod
    def setUpClass(cls):
        cls.src = tempfile.mkdtemp()
        cls.codex_dir = os.path.join(cls.src, "codex", "2026", "11", "13"); os.makedirs(cls.codex_dir)
        cls.claude_file = os.path.join(cls.src, "claude_usage.json")
        future = int(time.time()) + 3 * 3600
        cls.future = future
        line = ('{"type":"event_msg","payload":{"type":"token_count","rate_limits":{"limit_id":"codex","limit_name":null,'
                '"primary":{"used_percent":93.0,"window_minutes":300,"resets_at":%d},'
                '"secondary":{"used_percent":69.0,"window_minutes":10080,"resets_at":%d},"credits":{"has_credits":false}}}}\n') % (future, future + 86400)
        open(os.path.join(cls.codex_dir, "rollout-test.jsonl"), "w", encoding="utf-8").write('{"x":1}\n' + line)
        cls.write_claude(30, 10)
        cls._old = {k: os.environ.get(k) for k in ("CHAT_CODEX_SESSIONS", "CHAT_CLAUDE_USAGE", "CHAT_USAGE_POLL")}
        os.environ.update(CHAT_CODEX_SESSIONS=os.path.join(cls.src, "codex"), CHAT_CLAUDE_USAGE=cls.claude_file, CHAT_USAGE_POLL="0.5")
        super().setUpClass()

    @classmethod
    def write_claude(cls, used5, usedw):
        json.dump({"five_hour": {"used_percentage": used5, "resets_at": int(time.time()) + 7200},
                   "seven_day": {"used_percentage": usedw, "resets_at": int(time.time()) + 86400 * 3}, "ts": time.time()},
                  open(cls.claude_file, "w", encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass(); shutil.rmtree(cls.src, ignore_errors=True)
        for k, v in cls._old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def usage(self):
        return self.req("/api/messages?after=999999")[1]["usage"]

    def wait_for(self, cond, secs=6):
        end = time.time() + secs
        while time.time() < end:
            u = self.usage()
            if cond(u): return u
            time.sleep(0.3)
        return self.usage()

    def test_reads_codex_and_claude_automatically_and_manual_wins_when_newer(self):
        self.write_claude(30, 10)
        u = self.wait_for(lambda u: "codex" in u and u.get("claude", {}).get("pct") == 70.0)
        self.assertEqual(u["codex"]["pct"], 7.0); self.assertEqual(u["codex"]["week_pct"], 31.0)
        self.assertEqual(u["codex"]["by"], "auto"); self.assertTrue(u["codex"]["resets"])
        self.assertEqual(u["claude"]["pct"], 70.0); self.assertEqual(u["claude"]["week_pct"], 90.0)
        # un dato manual mas reciente que el automatico manda hasta que haya otro automatico mas nuevo
        self.assertEqual(self.req("/api/usage", {"from": "user", "who": "claude", "pct": 55})[0], 200)
        time.sleep(1.6)
        self.assertEqual(self.usage()["claude"]["pct"], 55.0)
        self.write_claude(10, 10)
        u = self.wait_for(lambda u: u["claude"]["pct"] == 90.0)
        self.assertEqual(u["claude"]["pct"], 90.0); self.assertEqual(u["claude"]["by"], "auto")

    def test_each_message_records_usage_and_spent_since_previous(self):
        self.write_claude(30, 10)
        self.wait_for(lambda u: u.get("claude", {}).get("pct") == 70.0)
        m1 = self.say("claude", "primero", to="codex")
        self.assertEqual(m1["use"]["left"], 70.0); self.assertIsNone(m1["use"]["spent"]); self.assertTrue(m1["use"]["auto"])
        self.write_claude(42, 10)
        self.wait_for(lambda u: u["claude"]["pct"] == 58.0)
        m2 = self.say("claude", "segundo", to="codex")
        self.assertEqual(m2["use"]["left"], 58.0); self.assertEqual(m2["use"]["spent"], 12.0)
        self.assertEqual(m2["use"]["week"], 90.0)
        self.assertNotIn("use", self.say("user", "hola", to="all"))          # los mensajes de la persona no llevan uso
        got = [m for m in self.req("/api/messages?after=0")[1]["messages"] if m["id"] == m2["id"]][0]
        self.assertEqual(got["use"]["spent"], 12.0)                           # persiste y sale en la API


class Pending(Base):
    def test_one_at_a_time_guide_and_no_reappearing(self):
        ids = [self.say("claude", "cuerpo secreto %d" % i, to="codex", type="info")["id"] for i in range(3)]
        self.assertTrue(all(i > 0 for i in ids))
        seen = []
        for n in range(3):
            code, out = self.cli("read", "codex", "--limit", "1")
            self.assertEqual(code, 0)
            seen.append(out)
        self.assertIn("cuerpo secreto 0", seen[0]); self.assertIn("quedan 2 sin leer", seen[0])
        self.assertNotIn("cuerpo secreto 1", seen[0])           # la guia no filtra cuerpos
        self.assertIn("cuerpo secreto 1", seen[1]); self.assertIn("quedan 1 sin leer", seen[1])
        self.assertIn("cuerpo secreto 2", seen[2]); self.assertNotIn("quedan", seen[2])
        code, out = self.cli("read", "codex")
        self.assertNotIn("cuerpo secreto", out)                # lo leido no reaparece

    def test_default_cli_limit_is_one(self):
        self.say("claude", "uno", to="codex"); self.say("claude", "dos", to="codex")
        code, out = self.cli("read", "codex")
        self.assertIn("uno", out); self.assertNotIn("dos", out)
        code, out = self.cli("read", "codex", "--limit", "0")
        self.assertIn("dos", out)

    def test_defer_pending_done(self):
        m = self.say("user", "recordar esto", to="claude")
        self.assertEqual(self.req("/api/defer", {"from": "claude", "id": m["id"]})[0], 200)
        self.assertEqual(self.req("/api/defer", {"from": "claude", "id": 99999})[0], 400)
        self.assertEqual(self.req("/api/defer", {"from": "claude", "id": "1"})[0], 400)
        code, out = self.cli("pending", "--as", "claude")
        self.assertIn("recordar esto", out)
        self.say("codex", "otro", to="claude")
        code, out = self.cli("read", "claude", "--limit", "5")
        self.assertIn("en pendiente: #%d" % m["id"], out)
        self.assertEqual(self.req("/api/done", {"from": "claude", "id": m["id"]})[0], 200)
        self.assertIn("sin pendientes", self.cli("pending", "--as", "claude")[1])
        st = json.load(open(os.path.join(self.tmp, "state.json")))
        self.assertIn("_deferred", st)


class Hardening(Base):
    def test_bad_input_never_hangs_or_crashes(self):
        for q in ("wait=nan", "wait=inf", "wait=-5", "limit=abc", "max_chars=-1"):
            t0 = time.time()
            s, r = self.req("/api/inbox?who=codex&" + q)
            self.assertLess(time.time() - t0, 3, q)  # nunca cuelga el long-poll
            self.assertIn(s, (200, 400), q)
        self.assertEqual(self.req("/api/messages?after=x")[0], 400)
        self.assertEqual(self.req("/api/messages", {"from": "claude", "text": "x", "to": "nadie"})[0], 400)
        self.assertEqual(self.req("/api/messages", {"from": "claude", "text": 5})[0], 400)
        self.assertEqual(self.req("/api/decide", {"from": "claude", "id": "../x", "status": "accepted"})[0], 400)
        self.assertEqual(self.req("/api/ack", {"from": "claude", "id": "1"})[0], 400)
        for body in (b"{no es json", b"[1,2]"):
            r = urllib.request.Request(self.url + "/api/messages", data=body, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(r, timeout=5)
            self.assertEqual(cm.exception.code, 400)

    def test_doc_init_malformed(self):
        for body in ({"name": "M1", "sections": []}, {"name": "M2", "sections": {"a": "x"}},
                     {"name": 5, "sections": {}}, {"name": "M3", "sections": {"a": {"owner": "nadie"}}}):
            self.assertEqual(self.req("/api/doc/init", dict(body, **{"from": "claude"}))[0], 400, body)
        for raw in (b"null", b"[]", b'"x"'):
            r = urllib.request.Request(self.url + "/api/doc", data=raw, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(r, timeout=5)
            self.assertEqual(cm.exception.code, 400)

    def test_doc_name_traversal_and_bad_version(self):
        self.assertEqual(self.req("/api/doc/init", {"from": "claude", "name": "../evil", "sections": {}})[0], 400)
        self.req("/api/doc/init", {"from": "claude", "name": "H", "sections": {"a": {"owner": "claude"}}})
        self.assertEqual(self.req("/api/doc", {"from": "claude", "name": "H", "section": "a", "text": "x"})[0], 400)
        self.assertEqual(self.req("/api/doc", {"from": "claude", "name": "H", "section": "a", "text": "x",
                                               "expected_version": "0"})[0], 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "..", "evil.json")))

    def test_unicode_truncation_recoverable_and_log_limited(self):
        txt = "\u00f1and\u00fa\u2713" * 100
        m = self.say("claude", txt, to="codex")
        r = self.req("/api/messages?after=0&limit=1&tail=1&max_chars=7")[1]["messages"]
        self.assertEqual(len(r), 1)
        self.assertEqual(len(r[0]["text"]), 7)
        self.assertEqual(self.req("/api/message?id=%d" % m["id"])[1]["text"], txt)

    def test_state_is_rollback_compatible(self):
        self.say("claude", "para estado", to="codex")
        self.req("/api/inbox?who=codex")
        st = json.load(open(os.path.join(self.tmp, "state.json")))
        self.assertIsInstance(st["codex"], int)  # cursores planos: tambien los lee v1


class Docs(Base):
    def test_docs_owner_version_history(self):
        s, _ = self.req("/api/doc/init", {"from": "claude", "name": "T", "sections": {
            "a": {"owner": "claude", "title": "A"}, "b": {"owner": "codex", "title": "B"}}})
        self.assertEqual(s, 200)
        self.assertEqual(self.req("/api/doc/init", {"from": "claude", "name": "T", "sections": {}})[0], 409)
        w = lambda frm, sec, v, t="hola": self.req("/api/doc", {"from": frm, "name": "T", "section": sec,
                                                                  "text": t, "expected_version": v})
        self.assertEqual(w("codex", "a", 0)[0], 403)        # no es suyo
        self.assertEqual(w("claude", "a", 0)[0], 200)
        s, r = w("claude", "a", 0); self.assertEqual((s, r["current"]), (409, 1))  # version vieja
        self.assertEqual(w("claude", "a", 1, "v2")[0], 200)
        self.assertEqual(w("claude", "zzz", 0)[0], 404)
        sec = self.req("/api/doc?name=T&section=a&history=1")[1]
        self.assertEqual((sec["version"], sec["text"], len(sec["history"])), (2, "v2", 2))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "docs", "T.md")))  # render legible

    def test_doc_cli(self):
        self.assertEqual(self.cli("doc", "init", "C", "x:claude:Equis", "--as", "claude")[0], 0)
        code, out = self.cli("doc", "set", "C", "x", "--version", "0", "-", "--as", "claude", stdin="texto\ncon lineas")
        self.assertEqual(code, 0)
        code, out = self.cli("doc", "set", "C", "x", "--version", "0", "otra", "--as", "claude")
        self.assertEqual(code, 4); self.assertIn("409", out)
        code, out = self.cli("doc", "get", "C", "x"); self.assertIn("con lineas", out)


class Compat(Base):
    def test_state_v1_flat_file_loads(self):
        d = tempfile.mkdtemp()
        json.dump({"claude": 3, "codex": 2, "user": 0}, open(os.path.join(d, "state.json"), "w"))
        with open(os.path.join(d, "messages.jsonl"), "w") as f:  # mensajes viejos sin type/reply_to
            for i in range(1, 5):
                f.write(json.dumps({"id": i, "ts": 1.0, "from": "claude", "to": "codex", "text": f"m{i}"}) + "\n")
        port = free_port()
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), str(port)],
                             env=dict(os.environ, CHAT_DATA=d), stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try: urllib.request.urlopen(f"http://127.0.0.1:{port}/api/decisions", timeout=1); break
                except Exception: time.sleep(0.1)
            r = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/inbox?who=codex").read())
            self.assertEqual([m["id"] for m in r["messages"]], [3, 4])  # cursor v1 respetado
        finally:
            p.terminate(); p.wait(); shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
