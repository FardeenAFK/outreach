"""Outreach engine: find jobs, enrich YouTube leads, export work for drafting, import drafts, report.

    python engine.py jobs                      # pull YT Jobs + other boards, score fits, export jobs needing proposals
    python engine.py job-leads                 # YT Jobs posters -> their X/IG/LinkedIn/Facebook as direct DM leads
    python engine.py discover [--niches 3]     # find active channels in your niches (yt-dlp search, no key)
    python engine.py add-channels FILE         # add candidate channels (ids / handles / urls / NexLev dump)
    python engine.py enrich [--limit N]        # fetch About + Videos pages, score, filter
    python engine.py to-draft [--limit N]      # export best leads for message drafting -> work/leads_to_draft.json
    python engine.py import-drafts FILE [--account me]
    python engine.py daily                     # EVERYTHING before drafting, one command (server, jobs, job-leads, discover, enrich, to-draft)
    python engine.py finish [work/drafts.json] # check drafts -> import -> report, one command
    python engine.py report                    # today's summary -> reports/YYYY-MM-DD.md
    python engine.py status                    # counts
    python engine.py team-sync                 # fetch teammates + their claimed leads, publish mine (daily/finish do it too)
    python engine.py team-join NAME            # add NAME to the team on GitHub and make this PC that person
"""
import argparse
import json
import random
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
import datetime as dt
from pathlib import Path

from core import (WORK, REPORTS, PLATFORMS, load_config, db, now, today, add_days,
                  effective_caps, log_event)
import team

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
# Platform message limits the drafter must respect.
CHAR_LIMITS = {"x": 450, "instagram": 450, "linkedin": 600, "facebook": 600}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- http helpers

def get(url, timeout=25, as_json=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "ignore")
    return json.loads(body) if as_json else body


def walk(o, key, out=None):
    out = [] if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                out.append(v)
            walk(v, key, out)
    elif isinstance(o, list):
        for v in o:
            walk(v, key, out)
    return out


def initial_data(html):
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    return json.loads(m.group(1)) if m else {}


def parse_count(text):
    """'1.86M subscribers' / '568K' / '12,345 views' -> int."""
    if not text:
        return None
    m = re.search(r"([\d.,]+)\s*([KMB])?", text.replace(" ", " "), re.I)
    if not m:
        return 0 if "no " in text.lower() else None
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1)
    return int(n * mult)


AGE_UNITS = [("mo", 30), ("month", 30), ("y", 365), ("year", 365), ("w", 7), ("week", 7),
             ("d", 1), ("day", 1), ("h", 0), ("hour", 0), ("min", 0), ("s", 0), ("second", 0)]


def parse_age_days(text):
    """'3 weeks ago' / '1y ago' / '2mo ago' / 'Streamed 5 days ago' -> days (int)."""
    m = re.search(r"(\d+)\s*([a-z]+)\s+ago", (text or "").lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    for prefix, days in AGE_UNITS:
        if unit.startswith(prefix):
            return n * days
    return None


def parse_duration(text):
    parts = [int(p) for p in text.split(":")] if text and re.fullmatch(r"\d+(:\d+){1,2}", text) else None
    if not parts:
        return None
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


# ---------------------------------------------------------------- YouTube channel enrichment

def channel_base(ref):
    ref = ref.strip()
    if ref.startswith("http"):
        u = urllib.parse.urlparse(ref)
        path = re.sub(r"/(about|videos|featured|shorts|streams)/?$", "", u.path.rstrip("/"))
        return "https://www.youtube.com" + path
    if ref.startswith("UC") and len(ref) == 24:
        return f"https://www.youtube.com/channel/{ref}"
    return "https://www.youtube.com/@" + ref.lstrip("@")


def classify_link(link):
    """By HOSTNAME, never substring: 'dropbox.com/s/..' or 'netflix.com/title/..' must not become X profiles."""
    try:
        host = (urllib.parse.urlparse(link if "//" in link else "https://" + link).hostname or "").lower()
    except ValueError:
        return "website"
    host = re.sub(r"^(www|m|mobile|web)\.", "", host)
    if host in ("x.com", "twitter.com"):
        return "x"
    if host == "instagram.com":
        return "instagram"
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in ("facebook.com", "fb.com"):
        return "facebook"
    return "website"


def normalize_profile(kind, link):
    link = link.strip()
    if not link.startswith("http"):
        link = "https://" + link
    u = urllib.parse.urlparse(link)
    path = u.path.rstrip("/")
    if kind == "x":
        handle = path.strip("/").split("/")[0] if path else ""
        ok = re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle) and handle.lower() not in ("share", "intent", "home", "i", "search", "hashtag")
        return f"https://x.com/{handle}" if ok else None
    if kind == "instagram":
        handle = path.strip("/").split("/")[0] if path else ""
        ok = re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle) and handle.lower() not in ("p", "reel", "reels", "explore", "accounts", "stories")
        return f"https://www.instagram.com/{handle}/" if ok else None
    if kind == "facebook":
        if not path or path.strip("/").split("/")[0] in ("sharer", "share", "groups", "events", "watch", "hashtag"):
            return None
        q = ("?" + u.query) if path.startswith("/profile.php") and u.query else ""
        return f"https://www.facebook.com{path}{q}"
    return link


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def fetch_channel(ref):
    base = channel_base(ref)
    about_html = get(base + "/about")
    data = initial_data(about_html)
    about = (walk(data, "aboutChannelViewModel") or [{}])[0]
    meta = (walk(data, "channelMetadataRenderer") or [{}])[0]
    if not meta.get("title") and not about:
        # consent page / layout change / block: fail loudly so the lead is RETRIED, not filtered forever
        raise RuntimeError("no channel metadata (consent page or YouTube layout change)")
    links = {"website": []}
    for l in walk(about, "channelExternalLinkViewModel"):
        link = (l.get("link") or {}).get("content") or ""
        kind = classify_link(link)
        if kind == "website":
            links["website"].append(link)
        elif kind not in links:
            prof = normalize_profile(kind, link)
            if prof:
                links[kind] = prof
    if not any(links.get(p) for p in PLATFORMS):  # socials often live behind a Linktree/Beacons page
        for site in [w for w in links["website"] if is_aggregator(w)][:1]:
            try:
                links.update({k: v for k, v in socials_from_page(site).items() if k not in links})
            except Exception:
                pass
    desc = about.get("description") or meta.get("description") or ""
    emails = [e for e in EMAIL_RE.findall(desc) if not e.lower().endswith((".png", ".jpg"))]

    vids_html = get(base + "/videos")
    vdata = initial_data(vids_html)
    videos = []
    for lk in walk(vdata, "lockupViewModel")[:15]:
        texts = [c for c in walk(lk.get("metadata", {}), "content") if isinstance(c, str)]
        badge = next((b.get("text") for b in walk(lk.get("contentImage", {}), "thumbnailBadgeViewModel")
                      if re.fullmatch(r"\d+(:\d+){1,2}", b.get("text") or "")), None)
        title = texts[0] if texts else ""
        views = next((parse_count(t) for t in texts[1:] if re.search(r"^[\d.,]+\s*[KMB]?( views)?$", t.strip(), re.I)), None)
        age = next((parse_age_days(t) for t in texts if parse_age_days(t) is not None), None)
        videos.append({"id": lk.get("contentId"), "title": title, "views": views, "age_days": age,
                       "duration": parse_duration(badge)})

    return {
        "id": about.get("channelId") or meta.get("externalId"),
        "name": meta.get("title") or "",
        "url": base,
        "subs": parse_count(about.get("subscriberCountText")),
        "country": about.get("country"),
        "description": desc,
        "links": links,
        "email": emails[0] if emails else None,
        "has_business_email": 1 if "signInForBusinessEmail" in about else 0,
        "videos": videos,
    }


def analyse(ch, cfg):
    """Return (score, signals, keep, reason) for an enriched channel."""
    f = cfg["filters"]
    vids = [v for v in ch["videos"] if v.get("age_days") is not None]
    signals, score = [], 0.0
    subs = ch["subs"] or 0
    last = min((v["age_days"] for v in vids), default=None)
    up90 = sum(1 for v in vids if v["age_days"] <= 90)
    views = [v["views"] for v in ch["videos"] if v.get("views") is not None]
    med = int(statistics.median(views)) if views else 0
    blob = (ch["description"] + " " + " ".join(v["title"] for v in ch["videos"])).lower()

    if not (f["min_subs"] <= subs <= f["max_subs"]):
        return 0, [], False, f"subs {subs} outside range"
    if last is None or last > f["max_days_since_upload"]:
        return 0, [], False, f"inactive (last upload {last} days ago)"
    if up90 < f.get("min_uploads_90d", 1):
        return 0, [], False, f"only {up90} uploads in 90 days"
    allow = f.get("countries_allow") or []
    if allow and ch.get("country") and ch["country"] not in allow:
        return 0, [], False, f"country {ch['country']} not in allow list"
    if "en" in f.get("languages", []):
        letters = [c for c in " ".join(v["title"] for v in ch["videos"][:8]) if c.isalpha()]
        if letters and sum(c.isascii() for c in letters) / len(letters) < 0.9:
            return 0, [], False, "titles not in English"
    bad = [k for k in f.get("exclude_keywords", []) if k in blob]
    if bad:
        return 0, [], False, f"excluded keyword: {bad[0]}"
    has_dm = any(ch["links"].get(p) for p in PLATFORMS)
    if f.get("require_dm_contact") and not has_dm:
        # still a good lead for the (future) email lane if they publish an address
        return 0, [], False, ("email only" if ch.get("email") else "no X/Instagram/LinkedIn on channel")

    score += 40
    if last <= 21:
        score += 15
        signals.append(f"active: last upload {last}d ago, {up90} uploads in 90 days")
    else:
        score += 8
        signals.append(f"slowing: last upload {last}d ago")

    # upload gaps growing
    ages = sorted(v["age_days"] for v in vids[:8])
    gaps = [b - a for a, b in zip(ages, ages[1:]) if b - a >= 0]
    if len(gaps) >= 4:
        recent_gap, older_gap = statistics.mean(gaps[:2]), statistics.mean(gaps[2:])
        if older_gap > 0 and recent_gap > 1.8 * older_gap and recent_gap >= 7:
            score += 10
            signals.append(f"upload gaps widening (~{recent_gap:.0f}d now vs ~{older_gap:.0f}d before)")

    # views declining - ignore videos under a week old, their views are still climbing
    views = [v["views"] for v in ch["videos"] if v.get("views") is not None and (v.get("age_days") or 0) >= 7]
    if len(views) >= 8:
        recent, older = statistics.median(views[:3]), statistics.median(views[3:10])
        if older > 0 and recent < 0.6 * older:
            score += 10
            signals.append(f"recent videos at ~{int(100 * recent / older)}% of their older views")
        elif older > 0 and recent > 1.5 * older:
            score += 4
            signals.append("views rising: growing channel, likely to need more output")

    durs = [v["duration"] for v in ch["videos"] if v.get("duration")]
    if durs and statistics.median(durs) >= 480:
        score += 10
        signals.append(f"long-form (median {int(statistics.median(durs) // 60)} min): edit-heavy, higher budget")

    hits = sorted({k for k in f.get("niche_bonus_keywords", []) if k in blob})
    if hits:
        score += min(15, 3 * len(hits))
        signals.append("niche fit: " + ", ".join(hits[:5]))
    if ch["links"].get("x"):
        score += 5
    if subs:
        signals.append(f"{subs:,} subs, median {med:,} views")
    return round(score, 1), signals, True, ""


# ---------------------------------------------------------------- commands

def cmd_add_channels(args):
    raw = json.loads(open(args.file, encoding="utf-8").read())
    refs = []

    def collect(o):
        if isinstance(o, str):
            if o.startswith(("UC", "@", "http")):
                refs.append(o)
        elif isinstance(o, dict):
            cid = o.get("channelId") or o.get("channel_id") or o.get("ytChannelId")
            if not cid and isinstance(o.get("id"), str) and o["id"].startswith("UC"):
                cid = o["id"]
            handle = o.get("handle") or o.get("customUrl") or o.get("username")
            if cid or handle:
                refs.append(cid or handle)
            else:
                for v in o.values():
                    collect(v)
        elif isinstance(o, list):
            for v in o:
                # NexLev dumps are [{type,text}] with JSON in text
                if isinstance(v, dict) and v.get("type") == "text" and isinstance(v.get("text"), str):
                    try:
                        collect(json.loads(v["text"]))
                    except ValueError:
                        pass
                else:
                    collect(v)

    collect(raw)
    con = db()
    added = 0
    for ref in dict.fromkeys(refs):
        key = ref if ref.startswith("UC") else "ref:" + ref
        exists = con.execute("SELECT 1 FROM leads WHERE id=? OR url=?", (key, channel_base(ref))).fetchone()
        if exists:
            continue
        con.execute("INSERT INTO leads(id, source, url, status, created, updated) VALUES (?,?,?,?,?,?)",
                    (key, args.source, channel_base(ref), "new", now(), now()))
        added += 1
    con.commit()
    print(f"found {len(refs)} refs, added {added} new leads")


# YouTube search filter "type = channel": results carry subscriber counts, so the
# 10K-500K range is applied before any page is fetched. Activity is checked in enrich.
SEARCH_FILTERS = {"channels": "EgIQAg%253D%253D"}


def cmd_discover(args):
    """Find ACTIVE channels in the configured niches via yt-dlp search (no API key needed).
    Rotates through niches so each day searches different ones."""
    import subprocess
    cfg = load_config()
    niches = cfg["filters"]["niches"]
    per_day = args.niches or 3
    start = (dt.date.today().toordinal() * per_day) % max(1, len(niches))
    picked = [niches[(start + i) % len(niches)] for i in range(min(per_day, len(niches)))]
    con = db()
    found = added = 0
    for niche in picked:
        for label, sp in SEARCH_FILTERS.items():
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(niche)}&sp={sp}&hl=en&gl=US"
            try:
                r = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-items", "1:100", "-J", url],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
                entries = json.loads(r.stdout).get("entries") or [] if (r.returncode == 0 and r.stdout) else None
            except (subprocess.TimeoutExpired, ValueError) as e:
                entries, r = None, None
                print(f"search failed for '{niche}' ({label}): {type(e).__name__}")
            if entries is None:
                if r is not None:
                    print(f"search failed for '{niche}' ({label}): {(r.stderr or '').strip()[-200:]}")
                continue
            for e in entries:
                cid = e.get("channel_id")
                if not cid:
                    continue
                found += 1
                subs = e.get("channel_follower_count")
                if subs is None or not (cfg["filters"]["min_subs"] <= subs <= cfg["filters"]["max_subs"]):
                    continue
                if con.execute("SELECT 1 FROM leads WHERE id=?", (cid,)).fetchone():
                    continue
                con.execute("INSERT INTO leads(id, source, url, name, status, note, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                            (cid, "ytsearch", f"https://www.youtube.com/channel/{cid}", e.get("channel") or "",
                             "new", f"found via '{niche}'", now(), now()))
                added += 1
            con.commit()
            time.sleep(random.uniform(2, 4))
    print(f"discover: searched {picked}, {found} results, {added} new channels")


def cmd_enrich(args):
    cfg = load_config()
    limit = args.limit or cfg["daily"]["new_leads_to_enrich"]
    con = db()
    rows = con.execute("SELECT * FROM leads WHERE status='new' ORDER BY created LIMIT ?", (limit,)).fetchall()
    kept = dropped = errors = 0
    for i, row in enumerate(rows):
        try:
            ch = fetch_channel(row["url"])
        except Exception as e:  # network/parse failure: keep for retry once, then mark error
            con.execute("UPDATE leads SET status=?, note=?, updated=? WHERE id=?",
                        ("error" if (row["note"] or "").startswith("retry") else "new",
                         f"retry: {e}"[:200], now(), row["id"]))
            con.commit()
            errors += 1
            continue
        score, signals, keep, reason = analyse(ch, cfg)
        new_id = ch["id"] or row["id"]
        if new_id != row["id"] and con.execute("SELECT 1 FROM leads WHERE id=?", (new_id,)).fetchone():
            con.execute("DELETE FROM leads WHERE id=?", (row["id"],))  # duplicate of a known channel
            con.commit()
            continue
        con.execute(
            """UPDATE leads SET id=?, name=?, url=?, subs=?, country=?, description=?, links=?, email=?,
               has_business_email=?, videos=?, last_upload_days=?, uploads_90d=?, median_views=?, signals=?,
               score=?, status=?, note=?, updated=? WHERE id=?""",
            (new_id, ch["name"], ch["url"], ch["subs"], ch["country"], ch["description"][:2000],
             json.dumps(ch["links"]), ch["email"], ch["has_business_email"], json.dumps(ch["videos"]),
             min((v["age_days"] for v in ch["videos"] if v.get("age_days") is not None), default=None),
             sum(1 for v in ch["videos"] if v.get("age_days") is not None and v["age_days"] <= 90),
             int(statistics.median([v["views"] for v in ch["videos"] if v.get("views") is not None] or [0])),
             json.dumps(signals), score,
             "enriched" if keep else ("email_only" if reason == "email only" else "filtered"),
             reason, now(), row["id"]))
        con.commit()
        kept += keep
        dropped += not keep
        print(f"[{i + 1}/{len(rows)}] {'KEEP' if keep else 'drop'} {ch['name'][:40]:40} {score:5}  {reason}")
        time.sleep(random.uniform(1.5, 3.5))  # polite pacing on public pages
    print(f"enriched: kept {kept}, filtered {dropped}, errors {errors}")


def pick_platform(con, cfg, links, account):
    """Choose ONE platform per lead - never pitch the same person on several apps at once.
    Balance toward the platform whose queue is emptiest relative to its cap."""
    caps = cfg["accounts"][account]["caps"]
    best, best_load = None, None
    fallback = set(cfg.get("fallback_platforms", ["facebook"]))
    main_ok = [p for p in PLATFORMS if p not in fallback and links.get(p) and caps.get(p)
               and (p != "linkedin" or "/in/" in links[p])]
    for p in cfg.get("platform_preference", PLATFORMS):
        if p in fallback and main_ok:
            continue  # Facebook only when there is no X / Instagram / personal LinkedIn
        if not links.get(p) or not caps.get(p):
            continue
        if p == "linkedin" and "/in/" not in links[p]:
            continue  # company/school pages can't take a connection request or a DM
        queued = con.execute("SELECT COUNT(*) FROM messages WHERE account=? AND platform=? AND status='queued' AND step=0",
                             (account, p)).fetchone()[0]
        load = queued / caps[p]
        if best is None or load < best_load - 0.25:
            best, best_load = p, load
    return best


def lead_owner(cfg, lead_id):
    """Deterministic split between teammates: the same lead id maps to the same person on every PC,
    so two machines never pitch the same channel, with no shared server needed (see team.py)."""
    return team.owner(team.members(cfg), lead_id)


def claimed_elsewhere(cfg):
    """Leads a teammate already queued/contacted, from the synced team branch (+ the old zip's list)."""
    return team.Taken(cfg)


def job_context(con, job_id):
    """For leads that came from a job post: what they asked for, so the DM can answer it."""
    if not job_id:
        return None
    j = con.execute("SELECT title, pay, url, posted, text FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(j) | {"text": (j["text"] or "")[:1800]} if j else None


def cmd_to_draft(args):
    cfg = load_config()
    account = args.account
    limit = args.limit or cfg["daily"]["new_leads_to_draft"]
    con = db()
    me = (cfg.get("team") or {}).get("me", "me")
    taken = claimed_elsewhere(cfg)
    for r in con.execute("SELECT id FROM leads WHERE status='enriched'").fetchall():
        if lead_owner(cfg, r["id"]) != me or r["id"] in taken:
            con.execute("UPDATE leads SET status='partner', note='belongs to teammate', updated=? WHERE id=?", (now(), r["id"]))
    con.commit()
    rows = con.execute("SELECT * FROM leads WHERE status='enriched' ORDER BY score DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        links = json.loads(r["links"] or "{}")
        plat = pick_platform(con, cfg, links, account)
        if not plat:
            continue
        vids = json.loads(r["videos"] or "[]")[:5]
        out.append({
            "id": r["id"], "name": r["name"], "channel": r["url"], "subs": r["subs"], "country": r["country"],
            "platform": plat, "profile_url": links[plat], "char_limit": CHAR_LIMITS[plat],
            "links_allowed": plat != "instagram",
            "followups_needed": len(cfg["followup_days"].get(plat, [])),
            "linkedin_flow": "connect-without-note, then DM after accept" if plat == "linkedin" else None,
            "signals": json.loads(r["signals"] or "[]"),
            "hiring": job_context(con, r["job_id"]),
            "description": (r["description"] or "")[:500],
            "recent_videos": [{"title": v["title"], "views": v["views"], "age_days": v["age_days"],
                               "duration_min": round(v["duration"] / 60, 1) if v.get("duration") else None,
                               "url": f"https://www.youtube.com/watch?v={v['id']}"} for v in vids],
        })
        con.execute("UPDATE leads SET status='to_draft', platform=?, updated=? WHERE id=?", (plat, now(), r["id"]))
    con.commit()
    path = WORK / "leads_to_draft.json"
    path.write_text(json.dumps({"account": account, "sender_name": cfg["sender_name"], "offer": cfg["offer"],
                                "sender_profile": cfg.get("profile", ""), "sender_us_based": bool(cfg.get("us_based")),
                                "leads": out}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out)} leads exported to {path}")


def cmd_import_drafts(args):
    cfg = load_config()
    data = json.loads(open(args.file, encoding="utf-8").read())
    account = getattr(args, "account", None) or data.get("account") or "me"
    if account not in cfg["accounts"]:
        sys.exit(f"unknown account '{account}' (config has: {', '.join(cfg['accounts'])})")
    con = db()
    n_leads = n_jobs = 0
    for d in data.get("leads", []):
        if not isinstance(d, dict) or not d.get("id"):
            print("skipped: lead entry without id"); continue
        lead = con.execute("SELECT * FROM leads WHERE id=?", (d["id"],)).fetchone()
        if not lead:
            print("unknown lead", d["id"])
            continue
        plat = {"twitter": "x"}.get((d.get("platform") or lead["platform"] or "").lower(),
                                     (d.get("platform") or lead["platform"] or "").lower())
        links = json.loads(lead["links"] or "{}")
        url = links.get(plat)
        first = (d.get("first") or "").strip()
        why = ("unknown platform '%s'" % plat if plat not in PLATFORMS else
               "no %s link" % plat if not url else
               "LinkedIn company page (can't connect/DM)" if plat == "linkedin" and "/in/" not in url else
               "empty first message" if not first else None)
        if why:
            print(f"skipped {lead['name']}: {why}"); continue
        if con.execute("SELECT 1 FROM messages WHERE lead_id=? AND account=? AND step=0", (d["id"], account)).fetchone():
            continue
        if plat == "linkedin":
            con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status, due_date) VALUES (?,?,?,?,?,?,?,?)",
                        (d["id"], account, plat, url, 0, "[Connect WITHOUT a note - free accounts get only 3 notes a month. The DM comes after they accept.]",
                         "queued", today()))
            con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status) VALUES (?,?,?,?,?,?,?)",
                        (d["id"], account, plat, url, 1, first, "waiting"))
            for k, fu in enumerate(d.get("followups") or [], start=2):
                con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status) VALUES (?,?,?,?,?,?,?)",
                            (d["id"], account, plat, url, k, fu.strip(), "waiting"))
        else:
            if len(first) > CHAR_LIMITS[plat]:
                print(f"warning: {lead['name']} first message {len(first)} chars > {CHAR_LIMITS[plat]}")
            con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status, due_date) VALUES (?,?,?,?,?,?,?,?)",
                        (d["id"], account, plat, url, 0, first, "queued", today()))
            for k, fu in enumerate((d.get("followups") or [])[:len(cfg["followup_days"].get(plat, []))], start=1):
                con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status) VALUES (?,?,?,?,?,?,?)",
                            (d["id"], account, plat, url, k, fu.strip(), "waiting"))
        con.execute("UPDATE leads SET status='queued', platform=?, updated=? WHERE id=?", (plat, now(), d["id"]))
        n_leads += 1
    for sid in data.get("skip_leads", []):
        con.execute("UPDATE messages SET status='cancelled' WHERE lead_id=? AND status IN ('queued','waiting')", (sid,))
        con.execute("UPDATE leads SET status='filtered', note='skipped by drafter', updated=? WHERE id=?", (now(), sid))
    for j in data.get("jobs", []):
        if not (j.get("proposal") or "").strip():
            print("skipped empty proposal", j.get("id")); continue
        con.execute("UPDATE jobs SET proposal=?, status='drafted', updated=? WHERE id=? AND status IN ('new','to_draft')",
                    (j["proposal"].strip(), now(), j["id"]))
        n_jobs += 1
    for jid in data.get("skip_jobs", []):
        con.execute("UPDATE jobs SET status='skipped', updated=? WHERE id=?", (now(), jid))
    con.commit()
    print(f"imported {n_leads} lead messages, {n_jobs} job proposals for account '{account}'")


# ---------------------------------------------------------------- jobs (YT Jobs)

def html_text(h):
    t = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", h or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;", " ", t)
    t = re.sub(r"&amp;", "&", t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def cmd_jobs(args):
    cfg = load_config()
    jc = cfg["jobs"]
    con = db()
    new = seen_pages = 0
    for page in range(1, (99 if getattr(args, "full", False) else jc.get("max_pages", 12)) + 1):
        try:
            d = get(f"https://app.ytjobs.co/api/jobs?page={page}", as_json=True)
        except Exception as e:
            print("ytjobs page", page, "failed:", e)
            break
        items = d.get("data") or []
        if not items:
            break
        known = 0

        def ignore(jid, title, why):
            con.execute("INSERT OR IGNORE INTO jobs(id, board, title, status, text, created, updated) VALUES (?,?,?,?,?,?,?)",
                        (jid, "YT Jobs", title, "ignored", why, now(), now()))
        for it in items:
            jid = f"ytjobs:{it['id']}"
            if con.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
                known += 1
                continue
            title = it.get("jobTitle") or ""
            tl = title.lower()
            if not any(k in tl for k in jc["include"]) or any(k in tl for k in jc["exclude"]):
                ignore(jid, title, "title filter")
                continue
            if (it.get("locationType") or "Remote").lower() != "remote":
                ignore(jid, title, "not remote")  # on-site / hybrid roles are no use from here
                continue
            try:
                det = get(f"https://app.ytjobs.co/api/jobs/{it['id']}", as_json=True)
                det = det.get("data", det)
            except Exception:
                det = it
            posted = det.get("createdAt") or ""
            try:
                age = (dt.date.today() - dt.datetime.strptime(posted, "%m/%d/%Y").date()).days
            except ValueError:
                age = None
            if age is not None and age > jc["max_age_days"]:
                ignore(jid, title, "too old")
                continue
            comp = it.get("company") or {}
            lo, hi = det.get("minSalary"), det.get("maxSalary")
            pay = f"${lo}-{hi}" if lo and hi else (f"${lo or hi}" if (lo or hi) else "unlisted")
            text = html_text(det.get("htmlContent") or det.get("description") or "")
            cats = ", ".join(c["name"] for c in (it.get("categories") or []) + (it.get("styles") or []))
            # Score on title + body only: many posts tick every category, which says nothing.
            blob = (title + " " + text).lower()
            fit = 40
            fit += 4 * min(5, sum(k in blob for k in cfg["filters"]["niche_bonus_keywords"]))
            fit += 12 if any(k in tl for k in ("documentary", "faceless", "true crime", "motion", "animat")) else 0
            fit += 8 if any(k in blob for k in ("after effects", "geolayers", "motion graphics", "animation")) else 0
            fit += 8 if pay != "unlisted" else 0
            fit += 6 if re.search(r"\b(ai|elevenlabs|ai voice|ai-generated)\b", blob) else 0
            fit -= 15 if re.search(r"\b(on[- ]camera|shoot days|relocate|in[- ]person|on[- ]site)\b", blob) else 0
            con.execute("""INSERT INTO jobs(id, board, title, company, channel_url, subs, pay, url, posted, text, fit, status, created, updated)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (jid, "YT Jobs", title, comp.get("name"), comp.get("ytLink"),
                         parse_count(str(comp.get("abvSubscribers") or "")), pay, f"https://ytjobs.co/job/{it['id']}",
                         posted, (f"[{cats}]\n" if cats else "") + text[:3000], min(fit, 100), "new", now(), now()))
            new += 1
            time.sleep(random.uniform(0.4, 1.0))
        con.commit()
        seen_pages += 1
        if items and known == len(items) and not getattr(args, "full", False):
            break  # reached jobs we already have (daily runs); --full keeps going to the last page
    extra = 0
    for board, jid, title, company, url, posted, text, pay in fetch_other_boards(cfg):
        key = f"{board.lower().replace(' ', '')}:{jid}"
        if con.execute("SELECT 1 FROM jobs WHERE id=?", (key,)).fetchone():
            continue
        con.execute("""INSERT INTO jobs(id, board, title, company, pay, url, posted, text, fit, status, created, updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key, board, title, company, pay, url, posted, text[:3000], 60, "new", now(), now()))
        extra += 1
    con.commit()
    print(f"other boards: {extra} new editing jobs")
    me = (cfg.get("team") or {}).get("me", "me")
    taken = claimed_elsewhere(cfg)
    con.execute("UPDATE jobs SET status='new' WHERE status='to_draft'")  # last export never drafted: offer again
    rows = [r for r in con.execute("SELECT * FROM jobs WHERE status='new' ORDER BY fit DESC").fetchall()
            if lead_owner(cfg, r["id"]) == me and r["id"] not in taken][:cfg["daily"]["jobs_to_draft"]]  # teammates never apply to the same post
    out = [{"id": r["id"], "title": r["title"], "company": r["company"], "channel": r["channel_url"], "pay": r["pay"],
            "url": r["url"], "posted": r["posted"], "fit": r["fit"], "text": r["text"][:2500]} for r in rows]
    for r in rows:
        con.execute("UPDATE jobs SET status='to_draft' WHERE id=?", (r["id"],))
    con.commit()
    (WORK / "jobs_to_draft.json").write_text(json.dumps({"sender_name": cfg["sender_name"],
                                                         "sender_us_based": bool(cfg.get("us_based")), "jobs": out}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"YT Jobs: scanned {seen_pages} pages, {new} new matching jobs, {len(out)} exported for proposals")


# ---------------------------------------------------------------- job posters -> direct DM leads

SOCIAL_RE = re.compile(r"(?<![\w.-])(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com|instagram\.com|linkedin\.com/(?:in|company)|facebook\.com)/[A-Za-z0-9_.\-/?=]+", re.I)


def video_channel(video_url):
    """Watch page -> (channel id, channel name)."""
    html = get(video_url)
    cid = re.search(r'"channelId":"(UC[\w-]{22})"', html)
    name = re.search(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"', html)
    return (cid.group(1) if cid else None), (json.loads(f'"{name.group(1)}"') if name else "")


AGGREGATORS = ("linktr.ee", "beacons.ai", "link.me", "bio.link", "lnk.bio", "campsite.bio", "hoo.be",
               "stan.store", "komi.io", "solo.to", "linkin.bio", "taplink", "carrd.co", "allmylinks")
JUNK_HANDLES = {"share", "sharer", "intent", "p", "reel", "reels", "explore", "accounts", "home", "hashtag",
                "legal", "privacy", "about", "help", "login", "signup", "tr", "plugins", "dialog", "linktree",
                "beacons", "wix", "squarespace", "shopify", "hubspot", "company", "in", "watch", "sharearticle"}


def socials_from_page(url):
    """Fetch a link-in-bio page or a company site and pull its own X / IG / LinkedIn / Facebook links."""
    if not url.startswith("http"):
        url = "https://" + url
    html = get(url, timeout=15)
    found = {}
    for raw in SOCIAL_RE.findall(html):
        kind = classify_link(raw)
        if kind not in PLATFORMS or kind in found:
            continue
        prof = normalize_profile(kind, raw.split('"')[0].split("\\")[0])
        if not prof:
            continue
        seg = [s for s in urllib.parse.urlparse(prof).path.split("/") if s]
        handle = (seg[1] if kind == "linkedin" and len(seg) > 1 else (seg[0] if seg else "")).lower()
        if not handle or handle in JUNK_HANDLES or len(handle) < 2 or (handle.isdigit() and len(handle) < 8):
            continue  # also drops xmlns junk like facebook.com/2008/fbml
        found[kind] = prof
    return found


def is_aggregator(link):
    return any(a in link.lower() for a in AGGREGATORS)


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def owns_post(cname, company, title, text):
    """True only if the post says this channel is THEIRS. 'Inspired by MagnatesMedia' or
    'think Trust Me Bro' name a style reference, and pitching that channel would be wrong."""
    n, c = cname.lower().strip(), (company or "").lower().strip()
    generic = {"the", "a", "of", "and", "channel", "tv", "official", "media", "studio", "studios", "productions",
               "production", "films", "film", "network", "group", "llc", "inc", "co", "agency", "youtube"}
    toks = lambda s: set(re.findall(r"[a-z0-9]+", s.lower())) - generic
    ct, nt = toks(c), toks(n)
    if (c and len(c) >= 4 and (n in c or c in n)) or (nt and ct and len(ct & nt) / len(nt) >= 0.5) or \
            (n in title.lower() and not re.search(r"style|like|inspired|similar|type of|such as", title.lower())):
        return True
    e = re.escape(n)
    return bool(re.search(rf"{e}\s+(is|are)\s+(looking|hiring|seeking|searching)|(for|at|join|on)\s+{e}\b|{e}'s\s+(team|channel)",
                          text.lower()))


def job_lead_score(j):
    """Hiring leads outrank everything; among them, fresh posts first (roles get filled), then fit."""
    try:
        age = (dt.date.today() - dt.datetime.strptime(j["posted"], "%m/%d/%Y").date()).days
    except (TypeError, ValueError):
        age = 30
    return round(100 + max(0, 30 - age) + (j["fit"] or 0) / 10, 1)


def cmd_job_leads(args):
    """For YT Jobs posts that fit, find the poster's X / Instagram / LinkedIn / Facebook so the sender can
    pitch them directly instead of applying through the board (no YouTube portfolio needed up front)."""
    cfg = load_config()
    con = db()
    rows = con.execute("""SELECT * FROM jobs WHERE board='YT Jobs' AND fit>=? AND status IN ('new','to_draft','drafted')
                          AND id NOT IN (SELECT job_id FROM leads WHERE job_id IS NOT NULL)
                          AND (dm_checked IS NULL OR dm_checked < ?)
                          ORDER BY created DESC, fit DESC LIMIT ?""",
                       (cfg["jobs"].get("dm_min_fit", 55), add_days(-7), args.limit)).fetchall()
    made = no_socials = 0
    for j in rows:
        jid = j["id"].split(":", 1)[1]
        try:
            det = get(f"https://app.ytjobs.co/api/jobs/{jid}", as_json=True)
            det = det.get("data", det)
        except Exception as e:
            print("detail failed", j["id"], e)
            continue
        comp = det.get("company") or {}
        haystack = " ".join([j["title"] or "", j["text"] or "", comp.get("name") or ""]).lower()
        refs = []
        for u in (comp.get("ytLink"), comp.get("website")):
            if u and "youtube.com/" in u and not u.rstrip("/").endswith("/channel"):
                refs.append(u)
        # Sample videos are often the poster's real show channel, but can also be "make it like this"
        # references from other creators: only trust one whose name appears in the post.
        text_handles = [norm_name(h) for h in re.findall(r"(?<![\w.])@([A-Za-z0-9_.\-]{3,30})\b", det.get("htmlContent") or "")]
        vc = {}
        for v in (det.get("youtubeVideos") or [])[:3]:
            try:
                cid, cname = video_channel(v["url"])
            except Exception:
                continue
            if cid and cname:
                vc.setdefault(cid, [cname, 0])[1] += 1
        for cid, (cname, n) in vc.items():
            nn = norm_name(cname)
            # an @handle in the post that names the same channel as the sample videos = their own channel
            handle_match = n >= 2 and len(nn) > 3 and any(nn in h or h in nn for h in text_handles if len(h) > 3)
            if len(cname) > 2 and (handle_match or owns_post(cname, comp.get("name") or "", j["title"] or "", j["text"] or "")):
                refs.insert(0, cid)
        links, main, emails = {"website": []}, None, []
        for text_link in SOCIAL_RE.findall(det.get("htmlContent") or ""):
            kind = classify_link(text_link)
            if kind in PLATFORMS and kind not in links:
                prof = normalize_profile(kind, text_link)
                if prof:
                    links[kind] = prof
        for ref in dict.fromkeys(refs):
            try:
                ch = fetch_channel(ref)
            except Exception:
                continue
            main = main or ch
            for k, v in ch["links"].items():
                if k != "website" and k not in links:
                    links[k] = v
            if ch.get("email"):
                emails.append(ch["email"])
            if not any(links.get(p) for p in PLATFORMS):  # try their link-in-bio page
                for site in [w for w in ch["links"].get("website", []) if is_aggregator(w)][:1]:
                    try:
                        for k, v in socials_from_page(site).items():
                            links.setdefault(k, v)
                    except Exception:
                        pass
            time.sleep(random.uniform(1.0, 2.0))
        site = comp.get("website") or ""
        if not any(links.get(p) for p in PLATFORMS) and site.startswith("http") and "youtube.com" not in site:
            try:  # agencies: their own site's footer
                for k, v in socials_from_page(site).items():
                    links.setdefault(k, v)
            except Exception:
                pass
        if not any(links.get(p) for p in PLATFORMS):
            no_socials += 1
            con.execute("UPDATE jobs SET dm_checked=? WHERE id=?", (today(), j["id"]))  # skip it for a week
            con.commit()
            print(f"  no socials: {j['title'][:50]} ({comp.get('name')})")
            continue
        lead_id = (main or {}).get("id") or f"job:{jid}"
        existing = con.execute("SELECT status FROM leads WHERE id=?", (lead_id,)).fetchone()
        if existing and existing["status"] in ("queued", "contacted", "replied", "won", "dead"):
            continue  # already talking to them
        signals = [f"HIRING on YT Jobs: {j['title']} ({j['pay']}, posted {j['posted']})"]
        if main and main.get("subs"):
            signals.append(f"channel: {main['name']} · {main['subs']:,} subs")
        vals = (lead_id, "ytjobs", (main or {}).get("name") or comp.get("name") or j["company"],
                (main or {}).get("url") or j["url"], (main or {}).get("subs"), (main or {}).get("country"),
                ((main or {}).get("description") or "")[:2000], json.dumps(links), emails[0] if emails else None,
                json.dumps((main or {}).get("videos") or []), json.dumps(signals), job_lead_score(j),
                "enriched", j["id"], now(), now())
        con.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        con.execute("""INSERT INTO leads(id, source, name, url, subs, country, description, links, email, videos,
                       signals, score, status, job_id, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
        con.execute("UPDATE jobs SET status='dm', updated=? WHERE id=?", (now(), j["id"]))
        con.commit()
        made += 1
        print(f"  DM lead: {vals[2][:40]:40} {', '.join(p for p in PLATFORMS if links.get(p))}  <- {j['title'][:40]}")
    print(f"job-leads: {made} posters with socials, {no_socials} without (those stay in the Jobs tab)")


# Other boards with public feeds. Editing jobs are rare there (~1-2/month each), so the title filter is strict.
BOARD_TITLE = re.compile(r"video[ -]?edit|motion (design|graphic)|youtube|video (creator|producer)|post[- ]production", re.I)


def fetch_other_boards(cfg):
    import xml.etree.ElementTree as ET
    out = []
    try:
        for j in get("https://remoteok.com/api?tag=video", as_json=True)[1:]:
            out.append(("Remote OK", j.get("id"), j.get("position"), j.get("company"), j.get("url"), (j.get("date") or "")[:10],
                        html_text(j.get("description") or ""),
                        f"${j['salary_min']}-{j['salary_max']}" if j.get("salary_min") else "unlisted"))
    except Exception as e:
        print("Remote OK failed:", e)
    try:
        for j in get("https://jobicy.com/api/v2/remote-jobs?count=50&tag=video", as_json=True)["jobs"]:
            out.append(("Jobicy", j["id"], j["jobTitle"], j.get("companyName"), j["url"], j["pubDate"][:10],
                        html_text(j.get("jobDescription") or ""), "unlisted"))
    except Exception as e:
        print("Jobicy failed:", e)
    for board, url in (("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss"), ("Dribbble", "https://dribbble.com/jobs.rss")):
        try:
            for it in ET.fromstring(get(url)).findall(".//item"):
                pub = it.findtext("pubDate") or ""
                try:
                    pub = dt.datetime.strptime(pub[:16], "%a, %d %b %Y").date().isoformat()
                except ValueError:
                    pub = ""
                out.append((board, it.findtext("guid") or it.findtext("link"), it.findtext("title"), "", it.findtext("link"),
                            pub, html_text(it.findtext("description") or ""), "unlisted"))
        except Exception as e:
            print(board, "failed:", e)
    keep = []
    for board, jid, title, company, url, posted, text, pay in out:
        if not title or not BOARD_TITLE.search(title) or re.search(r"interpreter|engineer|manager", title, re.I):
            continue
        try:
            if posted and (dt.date.today() - dt.date.fromisoformat(posted)).days > 30:
                continue
        except ValueError:
            pass
        keep.append((board, str(jid), title, company, url, posted, text, pay))
    return keep


# ---------------------------------------------------------------- reporting

def stats(con, cfg, account=None, day=None):
    day = day or today()
    acc_clause, params = ("AND account=?", [account]) if account else ("", [])
    sent = {p: con.execute(f"SELECT COUNT(*) FROM events WHERE day=? AND type='sent' AND platform=? {acc_clause}",
                           [day, p] + params).fetchone()[0] for p in PLATFORMS}
    week_start = add_days(-6, day)
    week = {t: con.execute(f"SELECT COUNT(*) FROM events WHERE day>=? AND day<=? AND type=? {acc_clause}",
                           [week_start, day, t] + params).fetchone()[0]
            for t in ("sent", "replied", "won", "applied")}
    total = {t: con.execute(f"SELECT COUNT(*) FROM events WHERE type=? {acc_clause}", [t] + params).fetchone()[0]
             for t in ("sent", "replied", "won", "applied")}
    queued = {p: con.execute(f"SELECT COUNT(*) FROM messages WHERE status='queued' AND due_date<=? AND platform=? {acc_clause}",
                             [day, p] + params).fetchone()[0] for p in PLATFORMS}
    leads = dict(con.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
    jobs = dict(con.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    applied_today = con.execute("SELECT COUNT(*) FROM events WHERE day=? AND type='applied'", (day,)).fetchone()[0]
    return {"day": day, "sent_today": sent, "queued_due": queued, "week": week, "total": total,
            "leads": leads, "jobs": jobs, "applied_today": applied_today,
            "caps": effective_caps(cfg, account) if account else None}


def cmd_report(args):
    cfg = load_config()
    con = db()
    lines = [f"# Outreach report - {today()}", ""]
    for acc, a in cfg["accounts"].items():
        s = stats(con, cfg, acc)
        lines.append(f"## {a['label']} ({acc})")
        lines.append("| platform | sent today | cap today | still queued |")
        lines.append("|---|---|---|---|")
        for p in PLATFORMS:
            lines.append(f"| {p} | {s['sent_today'][p]} | {s['caps'][p]} | {s['queued_due'][p]} |")
        w, t = s["week"], s["total"]
        rate = f"{100 * t['replied'] / t['sent']:.1f}%" if t["sent"] else "n/a"
        lines += ["", f"- Last 7 days: {w['sent']} sent, {w['replied']} replies, {w['won']} won, {w['applied']} job applications",
                  f"- All time: {t['sent']} sent, {t['replied']} replies (reply rate {rate}), {t['won']} won", ""]
    s = stats(con, cfg)
    lines += ["## Pipeline", f"- Leads by status: {s['leads']}", f"- Jobs by status: {s['jobs']}"]
    replies = con.execute("""SELECT l.name, l.url, e.platform, e.day FROM events e JOIN leads l ON l.id=e.lead_id
                             WHERE e.type='replied' AND e.day>=? ORDER BY e.ts DESC""", (add_days(-7),)).fetchall()
    if replies:
        lines += ["", "## Replies this week (follow up personally)"]
        lines += [f"- {r['name']} via {r['platform']} on {r['day']} - {r['url']}" for r in replies]
    text = "\n".join(lines) + "\n"
    (REPORTS / f"{today()}.md").write_text(text, encoding="utf-8")
    print(text)


def cmd_status(args):
    con = db()
    print("leads:", dict(con.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall()))
    print("messages:", dict(con.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall()))
    print("jobs:", dict(con.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()))


def ensure_server():
    """Start the local API (pythonw, detached) if it isn't answering."""
    import subprocess
    port = load_config().get("server_port", 8777)
    try:
        get(f"http://127.0.0.1:{port}/api/health", timeout=3)
        return "up"
    except Exception:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(pyw if pyw.exists() else sys.executable), str(Path(__file__).with_name("server.py"))],
                         cwd=str(Path(__file__).parent), creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_GROUP
        time.sleep(2)
        return "started"


def cmd_daily(args):
    """The whole collection half of the morning run in ONE command (one permission approval, no stalls).
    Every step is isolated: a failure is logged and the next step still runs."""
    steps = [("server", lambda: print("server:", ensure_server())),
             ("team", lambda: team.sync(db())),  # before drafting: newest member list + teammates' claims
             ("jobs", lambda: cmd_jobs(argparse.Namespace(full=False))),
             ("job-leads", lambda: cmd_job_leads(argparse.Namespace(limit=60))),
             ("discover", lambda: cmd_discover(argparse.Namespace(niches=None))),
             ("enrich", lambda: cmd_enrich(argparse.Namespace(limit=None))),
             ("to-draft", lambda: cmd_to_draft(argparse.Namespace(limit=None, account="me")))]
    results = {}
    for name, fn in steps:
        t0 = time.time()
        print(f"\n===== {name} =====", flush=True)
        try:
            fn()
            results[name] = f"ok ({time.time() - t0:.0f}s)"
        except Exception as e:  # keep going: a YouTube layout change must not kill the job scan
            results[name] = f"FAILED: {type(e).__name__}: {str(e)[:200]}"
            print(results[name], flush=True)
    n_leads = len(json.loads((WORK / "leads_to_draft.json").read_text(encoding="utf-8")).get("leads", [])) \
        if (WORK / "leads_to_draft.json").exists() else 0
    n_jobs = len(json.loads((WORK / "jobs_to_draft.json").read_text(encoding="utf-8")).get("jobs", [])) \
        if (WORK / "jobs_to_draft.json").exists() else 0
    summary = {"day": today(), "steps": results, "leads_to_draft": n_leads, "jobs_to_draft": n_jobs}
    (WORK / "daily-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("\n===== summary =====\n" + json.dumps(summary, indent=1))


def cmd_finish(args):
    """Check drafts -> import -> report, in one command. Refuses to import if the checker finds problems."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_drafts", Path(__file__).with_name("tools") / "check_drafts.py")
    chk = importlib.util.module_from_spec(spec); spec.loader.exec_module(chk)
    if chk.main(str(WORK / "leads_to_draft.json"), args.file) and not args.force:
        print("\nNOT IMPORTED: fix the problems above in", args.file, "and run finish again.")
        sys.exit(1)
    cmd_import_drafts(argparse.Namespace(file=args.file, account="me"))
    cmd_report(argparse.Namespace())
    try:  # publish today's claims so teammates skip these people
        team.sync(db())
    except Exception as e:
        print(f"team sync failed (not fatal, the next run retries): {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add-channels"); s.add_argument("file"); s.add_argument("--source", default="nexlev")
    s = sub.add_parser("discover"); s.add_argument("--niches", type=int, help="niches to search today (default 3)")
    s = sub.add_parser("enrich"); s.add_argument("--limit", type=int)
    s = sub.add_parser("to-draft"); s.add_argument("--limit", type=int); s.add_argument("--account", default="me")
    s = sub.add_parser("import-drafts"); s.add_argument("file"); s.add_argument("--account", default=None)
    s = sub.add_parser("job-leads"); s.add_argument("--limit", type=int, default=40)
    s = sub.add_parser("jobs"); s.add_argument("--full", action="store_true", help="scan every page, not just new ones")
    sub.add_parser("report"); sub.add_parser("status"); sub.add_parser("daily"); sub.add_parser("team-sync")
    s = sub.add_parser("team-join"); s.add_argument("name")
    s = sub.add_parser("finish"); s.add_argument("file", nargs="?", default=str(WORK / "drafts.json")); s.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"add-channels": cmd_add_channels, "discover": cmd_discover, "enrich": cmd_enrich, "to-draft": cmd_to_draft,
     "import-drafts": cmd_import_drafts, "jobs": cmd_jobs, "job-leads": cmd_job_leads, "report": cmd_report, "status": cmd_status,
     "daily": cmd_daily, "finish": cmd_finish,
     "team-sync": lambda a: team.sync(db()), "team-join": lambda a: team.join(db(), a.name)}[a.cmd](a)


if __name__ == "__main__":
    main()
