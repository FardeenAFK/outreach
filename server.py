"""Local API for the Outreach queue page / side panel. Binds 127.0.0.1 only.

GET  /api/health
GET  /api/queue?account=me        today's queue, capped per platform
GET  /api/pipeline?account=me     contacted leads awaiting a reply
GET  /api/jobs                    drafted job proposals
GET  /api/applications            the application tracker (hand-applied jobs + applied proposals)
GET  /api/stats?account=me
GET  /api/config                  everything the Settings tab edits
GET  /api/team                    team list, this PC's name, last sync, conflicts
GET  /api/check?q=URL_OR_HANDLE   free / mine / a teammate's
GET  /api/sources                 every job source (auto feeds, alert emails, manual links) + alert inbox status
POST /api/inbox         {action:"save"|"test"|"run"|"forget", user?, password?, host?}  (password is write-only)
POST /api/mark          {kind:"message"|"lead"|"job", id, action, body?, account?}
POST /api/applications  {action:"add"|"update"|"delete", id?, url, title, company, board, status, notes, force?}
POST /api/team/sync     sync the team branch now
POST /api/config        partial update (validated, clamped)
GET  /             the queue as a normal web page (no extension needed):
                   open http://127.0.0.1:<port>/ in the Chrome profile you send from
"""
import json
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from core import PLATFORMS, MAX_CAPS, load_config, save_config, db, now, today, add_days, effective_caps, log_event
from engine import stats, JOB_SOURCES, pay_boost
import team
import inbox

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SYNC_SOON = threading.Event()  # set after anything that creates a new claim (e.g. a hand-logged application)


def lead_card(con, lead_id):
    l = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not l:
        return {}
    vids = json.loads(l["videos"] or "[]")
    job = con.execute("SELECT title, url, pay FROM jobs WHERE id=?", (l["job_id"],)).fetchone() if l["job_id"] else None
    return {"name": l["name"], "channel": l["url"], "subs": l["subs"], "score": l["score"], "country": l["country"],
            "hiring": dict(job) if job else None,
            "signals": json.loads(l["signals"] or "[]"),
            "latest": vids[0]["title"] if vids else None,
            "latest_url": f"https://www.youtube.com/watch?v={vids[0]['id']}" if vids else None}


def email_footer(cfg):
    e = cfg.get("email") or {}
    lines = [cfg.get("sender_name", ""), e.get("address_line", ""), e.get("optout_line", "")]
    return "\n".join(x for x in lines if x and x.strip())


def queue(con, cfg, account):
    caps = effective_caps(cfg, account)
    out = {}
    footer = email_footer(cfg)
    # Low-DM mode also applies to first messages drafted before it was switched on: cold channels wait,
    # only people who posted that they're hiring get a social DM (follow-ups to people already contacted still show)
    warm = cfg.get("dm_mode", "all") == "warm"
    for p in PLATFORMS:
        sent = con.execute("SELECT COUNT(*) FROM events WHERE day=? AND type='sent' AND account=? AND platform=?",
                           (today(), account, p)).fetchone()[0]
        remaining = max(0, caps[p] - sent)
        rows = con.execute(
            """SELECT m.*, l.score FROM messages m JOIN leads l ON l.id=m.lead_id
               WHERE m.account=? AND m.platform=? AND m.status='queued' AND m.due_date<=?
                 AND m.step=(SELECT MIN(step) FROM messages WHERE lead_id=m.lead_id AND account=m.account
                             AND status IN ('queued','waiting'))
                 AND (? = 0 OR m.platform = 'email' OR l.job_id IS NOT NULL OR m.sent_at IS NOT NULL
                      OR EXISTS (SELECT 1 FROM messages s WHERE s.lead_id=m.lead_id AND s.status='sent'))
               ORDER BY (m.step>0) DESC, l.score DESC, m.id LIMIT ?""",
            (account, p, today(), 1 if warm else 0, remaining)).fetchall()
        out[p] = {"cap": caps[p], "sent": sent, "remaining": remaining,
                  "items": [{"id": r["id"], "lead_id": r["lead_id"], "platform": p, "step": r["step"],
                             "profile_url": r["profile_url"], "body": r["body"], "subject": r["subject"],
                             "footer": footer if p == "email" else None, **lead_card(con, r["lead_id"])}
                            for r in rows]}
    return out


def pipeline(con, account):
    rows = con.execute(
        """SELECT l.id, l.name, l.url, l.status, l.platform,
                  MAX(m.sent_at) AS last_sent, COUNT(m.sent_at) AS touches,
                  (SELECT profile_url FROM messages WHERE lead_id=l.id AND account=? LIMIT 1) AS profile_url,
                  (SELECT COUNT(*) FROM messages WHERE lead_id=l.id AND account=? AND platform='linkedin'
                      AND step=1 AND status='waiting') AS awaiting_accept,
                  (SELECT MIN(due_date) FROM messages WHERE lead_id=l.id AND account=? AND status='queued'
                      AND step>0) AS next_followup
           FROM leads l JOIN messages m ON m.lead_id=l.id AND m.account=?
           WHERE l.status IN ('contacted','replied','won')
           GROUP BY l.id ORDER BY l.status='replied' DESC, last_sent DESC LIMIT 300""",
        (account, account, account, account)).fetchall()
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
            cur = con.execute("UPDATE messages SET status='sent', sent_at=?, body=COALESCE(?, body), "
                              "subject=COALESCE(?, subject) WHERE id=? AND status='queued'",
                              (now(), p.get("body") if isinstance(p.get("body"), str) else None,
                               p.get("subject") if isinstance(p.get("subject"), str) else None, id_))
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
        elif action == "edit":  # save an edited draft without sending it
            if isinstance(p.get("body"), str):
                con.execute("UPDATE messages SET body=? WHERE id=? AND status IN ('queued','waiting')", (p["body"][:5000], id_))
            if isinstance(p.get("subject"), str):
                con.execute("UPDATE messages SET subject=? WHERE id=? AND status IN ('queued','waiting')", (p["subject"][:200], id_))
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
        job = con.execute("SELECT * FROM jobs WHERE id=?", (id_,)).fetchone()
        if not job:
            return {"error": "no such job"}
        if action in ("applied", "skipped"):
            con.execute("UPDATE jobs SET status=?, updated=? WHERE id=?", (action, now(), id_))
            log_event(con, "applied" if action == "applied" else "job_skipped", p.get("account"), "job", id_)
            if action == "applied":  # every application lands in the tracker, with a follow-up date
                con.execute("""INSERT INTO applications(board, title, company, url, job_id, status, notes, followup_due,
                               created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (job["board"], job["title"], job["company"], job["url"], id_, "applied", "drafted proposal",
                             add_days(7), now(), now()))
        elif action == "undo" and p.get("undo") in ("applied", "skipped"):
            con.execute("UPDATE jobs SET status='drafted', updated=? WHERE id=?", (now(), id_))
            ev = con.execute("SELECT id FROM events WHERE type=? AND lead_id=? ORDER BY id DESC LIMIT 1",
                             ("applied" if p["undo"] == "applied" else "job_skipped", id_)).fetchone()
            if ev:
                con.execute("DELETE FROM events WHERE id=?", (ev["id"],))
            if p["undo"] == "applied":
                con.execute("DELETE FROM applications WHERE job_id=?", (id_,))
        else:
            return {"error": "bad action"}
    else:
        return {"error": "bad kind"}
    con.commit()
    return {"ok": True}


# ---------------------------------------------------------------- application tracker

APP_STATUSES = ("applied", "interview", "won", "lost", "ghosted")


def board_of(url):
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    names = {"indeed.com": "Indeed", "upwork.com": "Upwork", "linkedin.com": "LinkedIn", "freelancer.com": "Freelancer",
             "ytjobs.co": "YT Jobs", "fiverr.com": "Fiverr", "onlinejobs.ph": "OnlineJobs.ph", "himalayas.app": "Himalayas",
             "jobgether.com": "Jobgether", "contra.com": "Contra", "workana.com": "Workana", "reddit.com": "Reddit",
             "x.com": "X", "twitter.com": "X", "facebook.com": "Facebook", "glassdoor.com": "Glassdoor",
             "peopleperhour.com": "PeoplePerHour", "remoteok.com": "Remote OK", "weworkremotely.com": "We Work Remotely"}
    for k, v in names.items():
        if host == k or host.endswith("." + k):
            return v
    return host or "other"


def applications(con):
    rows = con.execute("""SELECT * FROM applications ORDER BY
                          CASE status WHEN 'interview' THEN 0 WHEN 'applied' THEN 1 ELSE 2 END, created DESC LIMIT 500""").fetchall()
    return [dict(r) | {"followup_now": r["status"] == "applied" and (r["followup_due"] or "9") <= today()} for r in rows]


def app_action(con, cfg, p):
    act = p.get("action")
    clip = lambda k, n: (p.get(k) or "").strip()[:n] if isinstance(p.get(k), str) else ""
    if act == "add":
        url = clip("url", 600)
        if not url and not clip("title", 200):
            return 400, {"error": "add a link or a title"}
        if url and not re.match(r"^https?://", url):
            url = "https://" + url
        if url and not p.get("force"):
            c = team.check(con, cfg, url)
            if c["status"] in ("teammate", "mine"):
                return 409, {"error": c["status"], **c}
        status = clip("status", 20) if clip("status", 20) in APP_STATUSES else "applied"
        con.execute("""INSERT INTO applications(board, title, company, url, status, notes, followup_due, created, updated)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (clip("board", 60) or board_of(url), clip("title", 200), clip("company", 200), url, status,
                     clip("notes", 2000), add_days(7), now(), now()))
        log_event(con, "applied", p.get("account") or "me", "manual", None, url[:200])
        SYNC_SOON.set()
    elif act == "update":
        row = con.execute("SELECT * FROM applications WHERE id=?", (p.get("id"),)).fetchone()
        if not row:
            return 404, {"error": "no such application"}
        status = clip("status", 20)
        con.execute("UPDATE applications SET status=?, notes=?, followup_due=?, updated=? WHERE id=?",
                    (status if status in APP_STATUSES else row["status"],
                     clip("notes", 2000) if "notes" in p else row["notes"],
                     add_days(7) if p.get("followed_up") else row["followup_due"], now(), row["id"]))
        if status == "won" and row["status"] != "won":
            log_event(con, "won", p.get("account") or "me", "job", row["job_id"], row["url"])
    elif act == "delete":
        con.execute("DELETE FROM applications WHERE id=?", (p.get("id"),))
    else:
        return 400, {"error": "bad action"}
    con.commit()
    return 200, {"ok": True}


# ---------------------------------------------------------------- settings

def public_config(cfg):
    t = cfg.get("team") or {}
    return {"accounts": cfg["accounts"], "filters": cfg["filters"], "ramp": cfg.get("ramp"),
            "followup_days": cfg["followup_days"], "max_caps": MAX_CAPS, "platforms": list(PLATFORMS),
            "profile": {k: cfg.get(k) for k in ("sender_name", "offer", "profile", "us_based", "not_installed", "private_tools")},
            "email": cfg.get("email") or {}, "daily": cfg.get("daily") or {},
            "jobs": {k: (cfg.get("jobs") or {}).get(k) for k in ("max_age_days", "dm_min_fit", "search_keyword", "include", "exclude")}
                    | {"worldwide_only": (cfg.get("jobs") or {}).get("worldwide_only", True),
                       "skip_low_pay": (cfg.get("jobs") or {}).get("skip_low_pay", True)}
                    | {"sources": {n: ((cfg.get("jobs") or {}).get("sources") or {}).get(n, True) for n in JOB_SOURCES}},
            "team": {"me": t.get("me"), "members": t.get("members") or [], "solo": bool(t.get("solo"))},
            "dm_mode": cfg.get("dm_mode", "all"), "apply_kit": cfg.get("apply_kit") or []}


def _int(v, lo, hi):
    try:
        if isinstance(v, bool):
            return None
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return None


def _list(v, n=60, each=80):
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list):
        return None
    return [s.strip()[:each] for s in v if isinstance(s, str) and s.strip()][:n]


def update_config(cfg, p):
    """Partial, validated update. Blank / garbage values keep the old setting; numbers are clamped."""
    notes = []
    acc = p.get("account")
    if isinstance(p.get("caps"), dict) and acc in cfg["accounts"]:
        caps = dict(cfg["accounts"][acc]["caps"])
        for k, v in p["caps"].items():
            if k in PLATFORMS and (n := _int(v, 0, MAX_CAPS[k])) is not None:
                caps[k] = n
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
    pr = p.get("profile")
    if isinstance(pr, dict):
        for k, n in (("sender_name", 60), ("offer", 300), ("profile", 3000)):
            if isinstance(pr.get(k), str) and pr[k].strip():
                cfg[k] = pr[k].strip()[:n]
        if isinstance(pr.get("us_based"), bool):
            cfg["us_based"] = pr["us_based"]
        for k in ("not_installed", "private_tools"):
            if (lst := _list(pr.get(k), 30, 40)) is not None:
                cfg[k] = lst
        if isinstance(pr.get("sender_name"), str) and pr["sender_name"].strip():
            for a in cfg["accounts"].values():
                a["label"] = pr["sender_name"].strip()[:60]
    em = p.get("email")
    if isinstance(em, dict):
        e = cfg.setdefault("email", {})
        if em.get("client") in ("gmail", "outlook", "mailto"):
            e["client"] = em["client"]
        for k, n in (("address_line", 200), ("optout_line", 200)):
            if isinstance(em.get(k), str):
                e[k] = em[k].strip()[:n]
    if isinstance(p.get("followup_days"), dict):
        for k, v in p["followup_days"].items():
            if k in PLATFORMS and isinstance(v, list):
                days = sorted({d for d in (_int(x, 1, 30) for x in v) if d is not None})[:3]
                cfg["followup_days"][k] = days
    if isinstance(p.get("daily"), dict):
        d = cfg.setdefault("daily", {})
        for k, hi in (("new_leads_to_enrich", 300), ("new_leads_to_draft", 80), ("jobs_to_draft", 40)):
            if (n := _int(p["daily"].get(k), 1, hi)) is not None:
                d[k] = n
    if isinstance(p.get("ramp"), dict):
        r = cfg.setdefault("ramp", {})
        for k in ("week1_pct", "week2_pct"):
            if (n := _int(p["ramp"].get(k), 10, 100)) is not None:
                r[k] = n
    if isinstance(p.get("jobs"), dict):
        j = cfg.setdefault("jobs", {})
        if (n := _int(p["jobs"].get("max_age_days"), 3, 90)) is not None:
            j["max_age_days"] = n
        if isinstance(p["jobs"].get("search_keyword"), str) and p["jobs"]["search_keyword"].strip():
            j["search_keyword"] = p["jobs"]["search_keyword"].strip()[:60]
        for k in ("worldwide_only", "skip_low_pay"):
            if isinstance(p["jobs"].get(k), bool):
                j[k] = p["jobs"][k]
        if isinstance(p["jobs"].get("sources"), dict):
            src = j.setdefault("sources", {})
            for k, v in p["jobs"]["sources"].items():
                if isinstance(v, bool) and isinstance(k, str) and len(k) < 40:
                    src[k] = v
        for k in ("include", "exclude"):
            if (lst := _list(p["jobs"].get(k), 60, 40)) is not None and lst:
                j[k] = lst
    if p.get("dm_mode") in ("warm", "all"):
        cfg["dm_mode"] = p["dm_mode"]
    if isinstance(p.get("apply_kit"), list):
        kit = []
        for it in p["apply_kit"][:12]:
            if isinstance(it, dict) and isinstance(it.get("label"), str) and isinstance(it.get("text"), str) \
                    and it["label"].strip() and it["text"].strip():
                kit.append({"label": it["label"].strip()[:40], "text": it["text"].strip()[:1500]})
        cfg["apply_kit"] = kit
    # anti-spam law: a cold email needs the sender's postal address and an opt-out line
    e = cfg.get("email") or {}
    for a in cfg["accounts"].values():
        if a["caps"].get("email") and not (e.get("address_line") and e.get("optout_line")):
            a["caps"]["email"] = 0
            notes.append("Email stays off until you add your postal address and an opt-out line (anti-spam law).")
    return notes


# ---------------------------------------------------------------- job sources + alert inbox

SOURCES = Path(__file__).resolve().parent / "sources.json"


def inbox_status(con=None):
    login = inbox.load_login() or {}
    st = {"configured": bool(login), "user": inbox.masked(login.get("user")), "host": login.get("host"), "seen": {}}
    if con is not None and con.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_seen'").fetchone():
        st["seen"] = dict(con.execute("SELECT board, COUNT(*) FROM inbox_seen WHERE day>=? GROUP BY board",
                                      (add_days(-14),)).fetchall())
        st["jobs"] = dict(con.execute("SELECT board, SUM(jobs) FROM inbox_seen WHERE day>=? GROUP BY board",
                                      (add_days(-14),)).fetchall())
    return st


def sources(con):
    try:
        data = json.loads(SOURCES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"auto": [], "alerts": [], "manual": []}
    data["inbox"] = inbox_status(con)
    return data


def inbox_action(p):
    act = p.get("action")
    try:
        if act == "save":
            inbox.save_login(p.get("user"), p.get("password"), p.get("host") or None)
            return 200, {"ok": True, "inbox": inbox_status()}
        if act == "forget":
            inbox.forget_login()
            return 200, {"ok": True}
        if act == "test":
            return 200, {"ok": True, "counts": inbox.test_login()}
        if act == "run":
            con = db()
            try:
                return 200, {"ok": True, **inbox.run(con, load_config(), days=int(p.get("days") or 7))}
            finally:
                con.close()
    except (ValueError, RuntimeError, OSError) as e:
        return 400, {"error": str(e)[:300]}
    return 400, {"error": "bad action"}


# ---------------------------------------------------------------- HTTP

EXT = Path(__file__).resolve().parent / "extension"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}
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
  document.documentElement.classList.add("web");
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
        if account not in cfg["accounts"] and u.path in ("/api/queue", "/api/pipeline", "/api/stats"):
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
                rows = con.execute("SELECT id, board, title, company, channel_url, pay, url, posted, fit, proposal, spec_idea, substr(text, 1, 700) AS brief, "
                                   "(text LIKE '[agency]%' OR board LIKE '%(agency)') AS agency FROM jobs "
                                   "WHERE status='drafted' ORDER BY fit DESC").fetchall()
                return self._send(200, [dict(r) | {"pay_tier": pay_boost(r["pay"])} for r in rows])
            if u.path == "/api/applications":
                return self._send(200, applications(con))
            if u.path == "/api/stats":
                return self._send(200, stats(con, cfg, account))
            if u.path == "/api/config":
                return self._send(200, public_config(cfg))
            if u.path == "/api/team":
                ok, why = team.ready(cfg)
                return self._send(200, {"me": team.me(cfg), "members": team.members(cfg), "ready": ok, "why": why,
                                        "status": team.status(), "solo": bool((cfg.get("team") or {}).get("solo"))})
            if u.path == "/api/check":
                return self._send(200, team.check(con, cfg, q.get("q", "")[:500]))
            if u.path == "/api/sources":
                return self._send(200, sources(con))
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
        if path == "/api/inbox":
            return self._send(*inbox_action(p))
        if path in ("/api/mark", "/api/applications", "/api/team/sync"):
            con = db()
            try:
                if path == "/api/mark":
                    res = mark(con, cfg, p)
                    return self._send(400 if "error" in res else 200, res)
                if path == "/api/applications":
                    return self._send(*app_action(con, cfg, p))
                return self._send(200, team.sync(con, quiet=True))
            finally:
                con.close()
        if path == "/api/config":
            notes = update_config(cfg, p)
            save_config(cfg)
            return self._send(200, {"ok": True, "notes": notes})
        self._send(404, {"error": "not found"})


class ExclusiveServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows would otherwise let a 2nd server share the port
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def team_loop():
    """Keep the team list and teammates' claims fresh while the server runs: shortly after start, every 20 minutes,
    and a minute after anything new was claimed here. Failures only mean the last synced copy is used."""
    SYNC_SOON.wait(30)
    while True:
        SYNC_SOON.clear()
        try:
            con = db()
            try:
                team.sync(con, quiet=True)
            finally:
                con.close()
        except Exception:
            pass
        if SYNC_SOON.wait(20 * 60):
            time.sleep(60)  # batch a burst of new claims into one push


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else load_config().get("server_port", 8777)
    try:
        srv = ExclusiveServer(("127.0.0.1", port), Handler)
    except OSError:
        print(f"port {port} already in use: the Outreach server is probably running already")
        return
    threading.Thread(target=team_loop, daemon=True).start()
    print(f"Outreach server on http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
