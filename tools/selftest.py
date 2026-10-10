"""Regression tests for the Outreach system. Runs against COPIES of outreach.db and config.json,
with an in-process server on a spare port, so real data is never touched.
    python tools/selftest.py
"""
import argparse, json, os, shutil, sqlite3, sys, tempfile, threading, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="outreach-test-"))
src, dst = sqlite3.connect(ROOT / "outreach.db"), sqlite3.connect(tmp / "outreach.db")
src.backup(dst); src.close(); dst.close()
shutil.copy(ROOT / "config.json", tmp / "config.json")
os.environ["OUTREACH_DB"] = str(tmp / "outreach.db")
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
core.CONFIG_PATH, core.DB_PATH = tmp / "config.json", tmp / "outreach.db"
import server, engine  # noqa: E402

PORT = 8791
srv = server.ExclusiveServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
H = {"Origin": "chrome-extension://selftest", "Content-Type": "application/json"}
results = []


def call(path, body=None, headers=H, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(name, cond, detail=""):
    results.append((bool(cond), name, detail))


con = core.db()
# pick a queued first message on a platform with follow-ups
m = con.execute("""SELECT * FROM messages WHERE status='queued' AND step=0 AND platform='x'
                   AND lead_id IN (SELECT lead_id FROM messages WHERE step=1 AND status='waiting') LIMIT 1""").fetchone()
check("fixture: an X first message with follow-ups exists", m)
if m:
    ev = lambda: con.execute("SELECT COUNT(*) FROM events WHERE type='sent' AND lead_id=?", (m["lead_id"],)).fetchone()[0]
    s1 = call("/api/mark", {"kind": "message", "id": m["id"], "action": "sent"})
    s2 = call("/api/mark", {"kind": "message", "id": m["id"], "action": "sent"})
    check("double 'sent' counts once", ev() == 1 and s2[1].get("already"), f"events={ev()} second={s2}")
    fu = con.execute("SELECT step, status, due_date FROM messages WHERE lead_id=? AND step>0 ORDER BY step", (m["lead_id"],)).fetchall()
    check("follow-ups dated after first send", all(r["status"] == "queued" and r["due_date"] for r in fu), [tuple(r) for r in fu])
    # force both follow-ups due today: only the lowest step may show
    con.execute("UPDATE messages SET due_date=? WHERE lead_id=? AND step>0", (core.today(), m["lead_id"])); con.commit()
    q = call("/api/queue?account=me")[1]
    steps = [i["step"] for i in q["x"]["items"] if i["lead_id"] == m["lead_id"]]
    check("never two follow-ups to one person on one day", len(steps) <= 1, steps)
    con.execute("UPDATE messages SET due_date=? WHERE lead_id=? AND step=2", (core.add_days(9), m["lead_id"]))
    con.execute("UPDATE messages SET due_date=? WHERE lead_id=? AND step=1", (core.add_days(4), m["lead_id"])); con.commit()
    call("/api/mark", {"kind": "message", "id": m["id"], "action": "undo", "undo": "sent"})
    back = con.execute("SELECT status FROM messages WHERE id=?", (m["id"],)).fetchone()["status"]
    fu2 = [r["status"] for r in con.execute("SELECT status FROM messages WHERE lead_id=? AND step>0", (m["lead_id"],))]
    check("undo sent: message back in queue, event removed, follow-ups un-dated",
          back == "queued" and ev() == 0 and all(s == "waiting" for s in fu2), (back, ev(), fu2))
    call("/api/mark", {"kind": "message", "id": m["id"], "action": "skipped"})
    dead = con.execute("SELECT status FROM leads WHERE id=?", (m["lead_id"],)).fetchone()["status"]
    call("/api/mark", {"kind": "message", "id": m["id"], "action": "undo", "undo": "skipped"})
    restored = con.execute("SELECT status FROM leads WHERE id=?", (m["lead_id"],)).fetchone()["status"]
    check("skip then undo restores the lead", dead == "dead" and restored == "queued", (dead, restored))
    call("/api/mark", {"kind": "message", "id": m["id"], "action": "sent"})
    call("/api/mark", {"kind": "lead", "id": m["lead_id"], "action": "replied", "account": "me"})
    call("/api/mark", {"kind": "lead", "id": m["lead_id"], "action": "undo", "undo": "replied", "account": "me"})
    st = con.execute("SELECT status FROM leads WHERE id=?", (m["lead_id"],)).fetchone()["status"]
    check("replied then undo -> contacted again", st == "contacted", st)

# server hardening
check("foreign Host header is refused", call("/api/queue?account=me", headers={**H, "Host": "evil.example"})[0] == 403)
check("web-page Origin is refused", call("/api/queue?account=me", headers={**H, "Origin": "https://evil.example"})[0] == 403)
check("non-object body -> 400, not a reset", call("/api/mark", raw=b"[]")[0] == 400)
check("broken JSON -> 400", call("/api/mark", raw=b"{nope")[0] == 400)
before = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))["accounts"]["me"]["caps"]
code = call("/api/config", {"account": "me", "caps": {"x": "abc", "instagram": None, "linkedin": 999}})[0]
after = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))["accounts"]["me"]["caps"]
check("garbage caps keep old values, huge caps clamp", code == 200 and after["x"] == before["x"] and after["linkedin"] == 15, after)
code = call("/api/config", {"account": "me", "filters": {"max_subs": 0}})[0]
check("blank number field never wipes a filter", json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))["filters"]["max_subs"] > 0)


def hammer(i):
    return (call("/api/config", {"account": "me", "caps": {"x": 20}}) if i % 4 == 0 else call("/api/config"))[0]
with ThreadPoolExecutor(16) as ex:
    codes = list(ex.map(hammer, range(80)))
check("80 parallel config reads/saves: no errors", all(c == 200 for c in codes), sorted(set(codes)))

# engine units
cl = engine.classify_link
check("lookalike domains are not socials",
      [cl(u) for u in ["dropbox.com/s/x", "netflix.com/title/1", "roblox.com/a", "twitter.com/a", "instagram.com/b/"]]
      == ["website", "website", "website", "x", "instagram"])
check("junk handles rejected", engine.normalize_profile("x", "x.com/share") is None and
      engine.normalize_profile("x", "x.com/a.m3u8") is None)
op = engine.owns_post
check("style references are not owners",
      not op("MagnatesMedia", "Super B Agency", "Faceless Video Editor", "inspired by MagnatesMedia") and
      not op("Trust Me Bro", "Onto Something", "Editor", "think Trust Me Bro") and
      not op("SaintDon", "Ian", 'Editor for a "SaintDon" style video', "") and
      not op("Magnates Media", "Blue Media", "Editor", "") and not op("X", "", "Editor", ""))
check("real owners are recognised", op("vidIQ", "vidIQ", "Editor", "") and
      op("Dr Insanity", "Samuel Hylton", "Editor", "Dr Insanity is looking for an editor"))
check("team split is stable", engine.lead_owner(core.load_config(), "UCabc") == engine.lead_owner(core.load_config(), "UCabc"))
import hashlib, team  # noqa: E402
ids = [f"UC{hashlib.md5(str(i).encode()).hexdigest()[:22]}" for i in range(6000)] + [f"ytjobs:{i}" for i in range(2000)]
two = ["farde", "friend"]
check("two members keep the original sha1 % 2 split",
      all(team.owner(two, i) == two[int(hashlib.sha1(i.encode()).hexdigest(), 16) % 2] for i in ids))
moved_ok, sizes = True, []
for n in range(3, 7):
    a, b = two + [f"m{k}" for k in range(3, n)], two + [f"m{k}" for k in range(3, n + 1)]
    moved_ok &= all(team.owner(b, i) in (team.owner(a, i), b[-1]) for i in ids)
    share = sum(team.owner(b, i) == b[-1] for i in ids) / len(ids)
    sizes.append(round(share * len(b), 2))
check("adding a member only moves leads to the new member", moved_ok)
check("every member gets an even share", all(0.9 < s < 1.1 for s in sizes), sizes)
cfg_t = {"team": {"me": "farde", "members": two}}
team.CLAIMS_CACHE, real_cache = tmp / "team-claims.json", team.CLAIMS_CACHE
team.CLAIMS_CACHE.write_text(json.dumps({"friend": [team.claim_key("UCtaken")], "farde": [team.claim_key("UCmine")]}))
tk = team.Taken(cfg_t)
check("teammates' claims are taken, my own are not", "UCtaken" in tk and "UCmine" not in tk and "UCfree" not in tk)
# a teammate's claim on a social profile catches the same person under a different channel/job id
team.CLAIMS_CACHE.write_text(json.dumps({"friend": sorted(team.keys_for("https://twitter.com/SameGuy?s=20"))}))
tk = team.Taken(cfg_t)
check("same person under another id is caught by their profile",
      tk.who("UCother", "https://x.com/sameguy/") == "friend" and tk.who("@sameguy") == "friend" and not tk.who("https://x.com/other"))
team.CLAIMS_CACHE = real_cache
nl = team.norm_link
check("job links keep what identifies the post",
      nl("https://www.indeed.com/viewjob?jk=a1&from=serp") == nl("indeed.com/viewjob?jk=a1") != nl("https://indeed.com/viewjob?jk=b2")
      and nl("https://m.me/joe") == nl("https://facebook.com/joe") and nl("https://youtu.be/Ab1") == nl("https://youtube.com/watch?v=Ab1&si=q"))
check("solo install owns everything",
      team.owner(team.members({"team": {"me": "solo", "solo": True}}), "UCx") == "solo" and team.ready({"team": {"me": "solo", "solo": True}})[0])
check("a fresh install with no team list drafts nothing", not team.ready({"team": {"me": "newbie"}})[0]
      and not team.ready({"team": {"me": "newbie", "members": two}})[0])

# to-draft refuses (and says why) instead of guessing the split; writes to a temp work dir, never the real one
real_work, engine.WORK = engine.WORK, tmp
cfg_live = core.load_config()
blocked = dict(cfg_live, team={"me": "newbie"})
core.save_config(blocked)
engine.cmd_to_draft(argparse.Namespace(limit=None, account="me"))
exp = json.loads((tmp / "leads_to_draft.json").read_text(encoding="utf-8"))
check("to-draft blocked without a team list", exp.get("blocked") and exp["leads"] == [], exp.get("blocked"))
core.save_config(cfg_live)
engine.WORK = real_work

# application tracker: logged once, a second spelling of the same post is refused as "mine"
s1 = call("/api/applications", {"action": "add", "url": "https://www.indeed.com/viewjob?jk=selftest1", "title": "t"})
s2 = call("/api/applications", {"action": "add", "url": "indeed.com/viewjob?jk=selftest1&utm_source=x", "title": "t"})
s3 = call("/api/check?q=" + urllib.parse.quote("https://indeed.com/viewjob?jk=selftest1"))
check("tracker logs, refuses a duplicate, and Check knows it", s1[0] == 200 and s2[0] == 409 and s3[1].get("status") == "mine", (s1, s2, s3))
evil = dict(H, Origin="https://evil.example")
check("new endpoints refuse other websites", call("/api/applications", {"action": "add", "url": "x"}, headers=evil)[0] == 403
      and call("/api/team/sync", {}, headers=evil)[0] == 403)
r = call("/api/config", {"account": "me", "caps": {"email": 5}, "email": {"address_line": ""}})
check("email stays off without a postal address", core.load_config()["accounts"]["me"]["caps"]["email"] == 0 and r[1].get("notes"), r)
r = call("/api/config", {"account": "me", "caps": {"email": 99}, "email": {"address_line": "PO Box 1, Dhaka", "optout_line": "Reply no to stop."}})
q = None
if core.load_config()["accounts"]["me"]["caps"]["email"] == 15:
    con.execute("INSERT OR REPLACE INTO leads(id, name, url, email, status, links) VALUES ('UCmailtest','Mail Test','https://youtube.com/channel/UCmailtest','a@b.co','queued','{}')")
    con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status, due_date, subject) "
                "VALUES ('UCmailtest','me','email','mailto:a@b.co',0,'hello','queued',?, 'Quick idea for your intro')", (core.today(),))
    con.commit()
    q = call("/api/queue?account=me")[1]["email"]["items"]
check("email cap clamps to 15 and the queue carries subject + legal footer",
      q and q[0]["subject"] == "Quick idea for your intro" and "PO Box 1, Dhaka" in q[0]["footer"] and "Reply no" in q[0]["footer"], q)

srv.shutdown()
ok = sum(r[0] for r in results)
for passed, name, detail in results:
    print(("PASS " if passed else "FAIL ") + name + ("" if passed else f"   -> {detail}"))
print(f"\n{ok}/{len(results)} passed  (data copies in {tmp})")
sys.exit(0 if ok == len(results) else 1)
