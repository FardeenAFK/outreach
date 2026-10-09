"""Local API for the Outreach side panel. Binds 127.0.0.1 only.

GET  /api/health
GET  /api/queue?account=me        today's DM queue, capped per platform
GET  /api/pipeline?account=me     contacted leads awaiting a reply
GET  /api/jobs                    drafted job proposals
GET  /api/stats?account=me
GET  /api/config                  accounts / caps / filters
POST /api/mark     {kind:"message"|"lead"|"job", id, action, body?, account?}
POST /api/config   {caps?, filters?}  (partial update)
GET  /             the same queue panel as a normal web page (no extension needed):
                   open http://127.0.0.1:<port>/ in the Chrome profile you send from
"""
import json
import sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from core import PLATFORMS, MAX_CAPS, load_config, save_config, db, now, today, add_days, effective_caps, log_event
from engine import stats

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def lead_card(con, lead_id):
    l = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not l:
        return {}
    vids = json.loads(l["videos"] or "[]")
    job = con.execute("SELECT title, url, pay FROM jobs WHERE id=?", (l["job_id"],)).fetchone() if l["job_id"] else None
    return {"name": l["name"], "channel": l["url"], "subs": l["subs"], "score": l["score"],
            "hiring": dict(job) if job else None,
            "signals": json.loads(l["signals"] or "[]"),
            "latest": vids[0]["title"] if vids else None,
            "latest_url": f"https://www.youtube.com/watch?v={vids[0]['id']}" if vids else None}


def queue(con, cfg, account):
    caps = effective_caps(cfg, account)
    out = {}
    for p in PLATFORMS:
        sent = con.execute("SELECT COUNT(*) FROM events WHERE day=? AND type='sent' AND account=? AND platform=?",
                           (today(), account, p)).fetchone()[0]
        remaining = max(0, caps[p] - sent)
        rows = con.execute(
            """SELECT m.*, l.score FROM messages m JOIN leads l ON l.id=m.lead_id
               WHERE m.account=? AND m.platform=? AND m.status='queued' AND m.due_date<=?
                 AND m.step=(SELECT MIN(step) FROM messages WHERE lead_id=m.lead_id AND account=m.account
                             AND status IN ('queued','waiting'))
               ORDER BY (m.step>0) DESC, l.score DESC, m.id LIMIT ?""",
            (account, p, today(), remaining)).fetchall()
        out[p] = {"cap": caps[p], "sent": sent, "remaining": remaining,
                  "items": [{"id": r["id"], "lead_id": r["lead_id"], "platform": p, "step": r["step"],
                             "profile_url": r["profile_url"], "body": r["body"], **lead_card(con, r["lead_id"])}
                            for r in rows]}
    return out


def pipeline(con, account):
    rows = con.execute(
        """SELECT l.id, l.name, l.url, l.status, l.platform,
                  MAX(m.sent_at) AS last_sent, COUNT(m.sent_at) AS touches,
                  (SELECT profile_url FROM messages WHERE lead_id=l.id AND account=? LIMIT 1) AS profile_url,
                  (SELECT COUNT(*) FROM messages WHERE lead_id=l.id AND account=? AND platform='linkedin'
                      AND step=1 AND status='waiting') AS awaiting_accept
           FROM leads l JOIN messages m ON m.lead_id=l.id AND m.account=?
           WHERE l.status IN ('contacted','replied')
           GROUP BY l.id ORDER BY l.status='replied' DESC, last_sent DESC LIMIT 200""",
        (account, account, account)).fetchall()
    return [dict(r) for r in rows]


def schedule_followups(con, cfg, msg):
    """After a message is sent, date the next waiting follow-ups from today."""
    days = cfg["followup_days"].get(msg["platform"], [])
    first_fu_step = 2 if msg["platform"] == "linkedin" else 1
    if msg["step"] != (1 if msg["platform"] == "linkedin" else 0):
        return
    waiting = con.execute("""SELECT id, step FROM messages WHERE lead_id=? AND account=? AND platform=?
                             AND status='waiting' AND step>=? ORDER BY step""",
                          (msg["lead_id"], msg["account"], msg["platform"], first_fu_step)).fetchall()
    for i, w in enumerate(waiting):
        if i < len(days):
            con.execute("UPDATE messages SET status='queued', due_date=? WHERE id=?", (add_days(days[i]), w["id"]))
        else:
            con.execute("UPDATE messages SET status='cancelled' WHERE id=?", (w["id"],))


def undo_message(con, m, what):
    """Reverse an accidental Sent / Skip / Tomorrow on one message."""
    if what == "sent" and m["status"] == "sent":
        con.execute("UPDATE messages SET status='queued', sent_at=NULL, due_date=? WHERE id=?", (today(), m["id"]))
        ev = con.execute("""SELECT id FROM events WHERE type='sent' AND lead_id=? AND account=? AND platform=?
                            ORDER BY id DESC LIMIT 1""", (m["lead_id"], m["account"], m["platform"])).fetchone()
        if ev:
            con.execute("DELETE FROM events WHERE id=?", (ev["id"],))
        first_step = 1 if m["platform"] == "linkedin" else 0
        if m["step"] <= first_step:  # un-date the follow-ups this send had scheduled
            con.execute("""UPDATE messages SET status='waiting', due_date=NULL WHERE lead_id=? AND account=?
                           AND step>? AND status='queued'""", (m["lead_id"], m["account"], m["step"]))
        if not con.execute("SELECT 1 FROM messages WHERE lead_id=? AND status='sent'", (m["lead_id"],)).fetchone():
            con.execute("UPDATE leads SET status='queued', updated=? WHERE id=?", (now(), m["lead_id"]))
    elif what == "skipped" and m["status"] == "skipped":
        con.execute("UPDATE messages SET status='queued', due_date=? WHERE id=?", (today(), m["id"]))
        con.execute("""UPDATE messages SET status='waiting' WHERE lead_id=? AND account=? AND status='cancelled'
                       AND sent_at IS NULL""", (m["lead_id"], m["account"]))
        con.execute("UPDATE leads SET status='queued', note=NULL, updated=? WHERE id=? AND status='dead'",
                    (now(), m["lead_id"]))
        ev = con.execute("SELECT id FROM events WHERE type='skipped' AND lead_id=? ORDER BY id DESC LIMIT 1",
                         (m["lead_id"],)).fetchone()
        if ev:
            con.execute("DELETE FROM events WHERE id=?", (ev["id"],))
    elif what == "later":
        con.execute("UPDATE messages SET due_date=? WHERE id=?", (today(), m["id"]))


def cancel_pending(con, lead_id, account=None):
    q = "UPDATE messages SET status='cancelled' WHERE lead_id=? AND status IN ('queued','waiting')"
    con.execute(q + (" AND account=?" if account else ""), (lead_id, account) if account else (lead_id,))


def mark(con, cfg, p):
    kind, action, id_ = p.get("kind"), p.get("action"), p.get("id")
    if kind == "message":
        m = con.execute("SELECT * FROM messages WHERE id=?", (id_,)).fetchone()
        if not m:
            return {"error": "no such message"}
        if action == "sent":
            cur = con.execute("UPDATE messages SET status='sent', sent_at=?, body=COALESCE(?, body) WHERE id=? AND status='queued'",
                              (now(), p.get("body") if isinstance(p.get("body"), str) else None, id_))
            if not cur.rowcount:
                return {"ok": True, "already": True}  # double click / second tab: count it once
            con.execute("""UPDATE messages SET due_date=MAX(due_date, ?) WHERE lead_id=? AND account=? AND status='queued'
                           AND step>?""", (add_days(3), m["lead_id"], m["account"], m["step"]))
            con.execute("UPDATE leads SET status='contacted', updated=? WHERE id=? AND status NOT IN ('replied','won')",
                        (now(), m["lead_id"]))
            log_event(con, "sent", m["account"], m["platform"], m["lead_id"], f"step {m['step']}")
            schedule_followups(con, cfg, m)
        elif action == "skipped":
            con.execute("UPDATE messages SET status='skipped' WHERE id=?", (id_,))
            if m["step"] == 0:  # skipping a first touch drops the lead
                cancel_pending(con, m["lead_id"], m["account"])
                con.execute("UPDATE leads SET status='dead', note='skipped in panel', updated=? WHERE id=?",
                            (now(), m["lead_id"]))
            log_event(con, "skipped", m["account"], m["platform"], m["lead_id"], f"step {m['step']}")
        elif action == "later":
            con.execute("UPDATE messages SET due_date=? WHERE id=?", (add_days(1), id_))
        elif action == "undo":
            undo_message(con, m, p.get("undo"))
        else:
            return {"error": "bad action"}
    elif kind == "lead":
        account = p.get("account")
        lead = con.execute("SELECT * FROM leads WHERE id=?", (id_,)).fetchone()
        if not lead:
            return {"error": "no such lead"}
        if action == "accepted":  # LinkedIn connection accepted -> queue the DM today
            con.execute("""UPDATE messages SET status='queued', due_date=? WHERE lead_id=? AND account=?
                           AND platform='linkedin' AND step=1 AND status='waiting'""", (today(), id_, account))
        elif action in ("replied", "won", "dead"):
            cancel_pending(con, id_)
            con.execute("UPDATE leads SET status=?, updated=? WHERE id=?", (action, now(), id_))
            log_event(con, action, account, lead["platform"], id_)
        elif action == "undo" and p.get("undo") in ("replied", "won", "dead"):
            last = con.execute("SELECT id FROM events WHERE type=? AND lead_id=? ORDER BY id DESC LIMIT 1",
                               (p["undo"], id_)).fetchone()
            if last:
                con.execute("DELETE FROM events WHERE id=?", (last["id"],))
            con.execute("UPDATE leads SET status='contacted', updated=? WHERE id=?", (now(), id_))
            con.execute("""UPDATE messages SET status='queued', due_date=? WHERE lead_id=? AND status='cancelled'
                           AND sent_at IS NULL""", (add_days(1), id_))
        else:
            return {"error": "bad action"}
    elif kind == "job":
        if action not in ("applied", "skipped"):
            return {"error": "bad action"}
        con.execute("UPDATE jobs SET status=?, updated=? WHERE id=?", (action, now(), id_))
        log_event(con, "applied" if action == "applied" else "job_skipped", p.get("account"), "job", id_)
    else:
        return {"error": "bad kind"}
    con.commit()
    return {"ok": True}


EXT = Path(__file__).resolve().parent / "extension"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".png": "image/png"}
# Web-page mode: stands in for the chrome.* APIs the side panel uses. Skipped inside the real extension.
WEB_SHIM = b"""<script>
(function () {
  const c = window.chrome || (window.chrome = {});
  if (c.tabs && c.storage) return;
  const def = (k, v) => { try { Object.defineProperty(c, k, { value: v, configurable: true }); } catch (e) { c[k] = v; } };
  def("storage", { local: {
    get: async (keys) => {
      const o = { server: location.origin };
      try { for (const k of [].concat(keys || [])) { if (k === "server") continue;
        const v = localStorage.getItem("outreach." + k); if (v !== null) o[k] = JSON.parse(v); } } catch (e) {}
      return o;
    },
    set: async (o) => { try { for (const k in o) if (k !== "server") localStorage.setItem("outreach." + k, JSON.stringify(o[k])); } catch (e) {} },
  } });
  // One reused tab named outreach_dm; opener cut so the social site can't touch this page.
  const open = (o) => {
    const w = window.open("about:blank", "outreach_dm");
    if (!w) { alert("The browser blocked the new tab. Allow pop-ups for " + location.host + ", then click again."); return; }
    try { w.opener = null; } catch (e) {}
    w.location.href = o.url;
    try { w.focus(); } catch (e) {}
  };
  def("tabs", { query: async () => [], update: (id, o) => open(o), create: open });
})();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _origin_ok(self):
        # Only the extension (or no browser origin, e.g. curl) may call this API.
        # Blocks any web page from driving it through the user's browser (localhost CSRF).
        o = self.headers.get("Origin")
        port = self.server.server_address[1]
        return o is None or o.startswith("chrome-extension://") or o in (
            f"http://127.0.0.1:{port}", f"http://localhost:{port}")  # the panel served by this server

    def _send(self, code, obj):
        body = b"" if code == 204 else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        o = self.headers.get("Origin")
        if o and o.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._origin_ok():
            return self._send(403, {"error": "origin"})
        self._send(204, {})

    def _host_ok(self):
        # DNS-rebinding guard: only answer requests addressed to this machine
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        try:
            self._get()
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            self._post()
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _static(self, path):
        name = "sidepanel.html" if path in ("/", "/index.html") else path.lstrip("/")
        f = (EXT / name).resolve()
        if EXT not in f.parents or not f.is_file() or f.suffix not in STATIC_TYPES:
            return self._send(404, {"error": "not found"})
        body = f.read_bytes()
        if f.name == "sidepanel.html":
            body = body.replace(b"<script src=", WEB_SHIM + b"<script src=", 1)
        self.send_response(200)
        self.send_header("Content-Type", STATIC_TYPES[f.suffix])
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get(self):
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        if not self._origin_ok():
            return self._send(403, {"error": "origin"})
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        cfg = load_config()
        account = q.get("account", "me")
        if account not in cfg["accounts"] and u.path not in ("/api/health", "/api/config", "/api/jobs"):
            return self._send(400, {"error": f"unknown account '{account}'"})
        con = db()
        try:
            if u.path == "/api/health":
                return self._send(200, {"ok": True, "day": today()})
            if u.path == "/api/queue":
                return self._send(200, queue(con, cfg, account))
            if u.path == "/api/pipeline":
                return self._send(200, pipeline(con, account))
            if u.path == "/api/jobs":
                rows = con.execute("SELECT id, title, company, channel_url, pay, url, posted, fit, proposal FROM jobs "
                                   "WHERE status='drafted' ORDER BY fit DESC").fetchall()
                return self._send(200, [dict(r) for r in rows])
            if u.path == "/api/stats":
                return self._send(200, stats(con, cfg, account))
            if u.path == "/api/config":
                return self._send(200, {"accounts": cfg["accounts"], "filters": cfg["filters"],
                                        "ramp": cfg.get("ramp"), "followup_days": cfg["followup_days"]})
            self._send(404, {"error": "not found"})
        finally:
            con.close()

    def _post(self):
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        if not self._origin_ok():
            return self._send(403, {"error": "origin"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            p = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if not isinstance(p, dict) or isinstance(p.get("id"), (list, dict)):
            return self._send(400, {"error": "bad request"})
        cfg = load_config()
        path = urlparse(self.path).path
        if path == "/api/mark":
            con = db()
            try:
                res = mark(con, cfg, p)
            finally:
                con.close()
            return self._send(400 if "error" in res else 200, res)
        if path == "/api/config":
            acc = p.get("account")
            if "caps" in p and acc in cfg["accounts"]:
                caps = dict(cfg["accounts"][acc]["caps"])
                for k, v in (p["caps"] if isinstance(p["caps"], dict) else {}).items():
                    try:
                        if k in PLATFORMS:
                            caps[k] = max(0, min(int(v), MAX_CAPS[k]))
                    except (TypeError, ValueError):
                        pass  # blank/garbage field keeps the old value
                cfg["accounts"][acc]["caps"] = caps
            if isinstance(p.get("filters"), dict):
                for k, v in p["filters"].items():
                    if k not in cfg["filters"]:
                        continue
                    old = cfg["filters"][k]
                    if isinstance(old, (bool, list)):
                        if type(v) is type(old):
                            cfg["filters"][k] = v
                    elif isinstance(old, (int, float)):
                        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                            cfg["filters"][k] = int(v)  # a blank field (0) never wipes a filter
            save_config(cfg)
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


class ExclusiveServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows would otherwise let a 2nd server share the port
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else load_config().get("server_port", 8765)
    try:
        srv = ExclusiveServer(("127.0.0.1", port), Handler)
    except OSError:
        print(f"port {port} already in use: the Outreach server is probably running already")
        return
    print(f"Outreach server on http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
