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
    python engine.py team-sync [--take-over]   # fetch teammates + their claimed leads, publish mine (daily/finish do it too)
    python engine.py team-join NAME            # add NAME to the team on GitHub and make this PC that person
    python engine.py check URL_OR_HANDLE       # before messaging anyone by hand: free, mine, or a teammate's?
    python engine.py inbox [--days 3] [--dry-run]  # job-alert emails (Indeed, LinkedIn, Upwork...) -> jobs to apply to
"""
import argparse
import hashlib
import html
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
CHAR_LIMITS = {"x": 450, "instagram": 450, "linkedin": 600, "facebook": 600, "email": 1500}

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
    path = WORK / "leads_to_draft.json"
    ok, why = team.ready(cfg)
    if not ok:  # never guess the split: a wrong guess means two people pitching the same client
        path.write_text(json.dumps({"account": account, "sender_name": cfg["sender_name"], "leads": [],
                                    "blocked": why}, indent=1, ensure_ascii=False), encoding="utf-8")
        print("to-draft: NOT DRAFTING TODAY:", why)
        return
    me = team.me(cfg)
    taken = claimed_elsewhere(cfg)
    email_on = effective_caps(cfg, account).get("email", 0) > 0
    if email_on:  # channels whose only public contact is an email become leads for the email lane
        con.execute("UPDATE leads SET status='enriched', updated=? WHERE status='email_only'", (now(),))
    mine = {team.norm_link(u) for (u,) in con.execute("SELECT DISTINCT profile_url FROM messages WHERE profile_url IS NOT NULL")}
    mine.discard("")
    for r in con.execute("SELECT * FROM leads WHERE status='enriched'").fetchall():
        idents = team.lead_idents(r)
        who = taken.who(*idents)
        if lead_owner(cfg, r["id"]) != me or who:
            con.execute("UPDATE leads SET status='partner', note=?, updated=? WHERE id=?",
                        (f"belongs to {who}" if who and who != "teammate" else "belongs to teammate", now(), r["id"]))
        elif any(team.norm_link(v) in mine for v in idents[1:]):  # same person under another channel/job id
            con.execute("UPDATE leads SET status='filtered', note='same person as a lead you already messaged', updated=? "
                        "WHERE id=?", (now(), r["id"]))
    con.commit()
    # Low-DM mode: social DMs only to people who publicly posted that they're hiring (warm, rarely reported).
    # Cold channels can still get an email if the email lane is on; otherwise they wait.
    warm = cfg.get("dm_mode", "all") == "warm"
    where = "status='enriched'" + ((" AND (job_id IS NOT NULL" + (" OR (email IS NOT NULL AND email != '')" if email_on else "")
                                    + ")") if warm else "")
    rows = con.execute(f"SELECT * FROM leads WHERE {where} ORDER BY job_id IS NULL, score DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        links = json.loads(r["links"] or "{}")
        if email_on and r["email"]:
            links["email"] = "mailto:" + r["email"].strip()
        if warm and not r["job_id"]:
            links = {"email": links["email"]} if "email" in links else {}
        plat = pick_platform(con, cfg, links, account)
        if not plat:
            continue
        vids = json.loads(r["videos"] or "[]")[:5]
        out.append({
            "id": r["id"], "name": r["name"], "channel": r["url"], "subs": r["subs"], "country": r["country"],
            "platform": plat, "profile_url": links[plat], "char_limit": CHAR_LIMITS[plat],
            "links_allowed": plat not in ("instagram", "facebook"),
            "subject_needed": plat == "email",
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
        url = links.get(plat) if plat != "email" else ("mailto:" + lead["email"].strip() if lead["email"] else None)
        first = (d.get("first") or "").strip()
        subject = (d.get("subject") or "").strip() if plat == "email" else None
        why = ("unknown platform '%s'" % plat if plat not in PLATFORMS else
               "no %s link" % plat if not url else
               "LinkedIn company page (can't connect/DM)" if plat == "linkedin" and "/in/" not in url else
               "empty first message" if not first else
               "email without a subject" if plat == "email" and not subject else None)
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
            con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status, due_date, subject) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", (d["id"], account, plat, url, 0, first, "queued", today(), subject))
            for k, fu in enumerate((d.get("followups") or [])[:len(cfg["followup_days"].get(plat, []))], start=1):
                con.execute("INSERT INTO messages(lead_id, account, platform, profile_url, step, body, status, subject) "
                            "VALUES (?,?,?,?,?,?,?,?)", (d["id"], account, plat, url, k, fu.strip(), "waiting",
                                                         ("Re: " + subject) if subject else None))
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
            per = {1: "video", 3: "year", 5: "hour"}.get(det.get("jobType") or it.get("jobType"))  # YT Jobs pay period
            pay = f"${lo}-{hi}" if lo and hi else (f"${lo or hi}" if (lo or hi) else "unlisted")
            if pay != "unlisted" and per:
                pay += {"video": "/project", "year": "/yr", "hour": "/hr"}[per]
            loc = det.get("locationDetails") or it.get("locationDetails")
            if isinstance(loc, dict) and loc.get("countryName") and jc.get("worldwide_only", True):
                ignore(jid, title, f"locked to {loc.get('countryName')}")  # international, open-to-anyone roles only
                continue
            agency = comp.get("type") == 1  # a company or agency, not a creator's own channel
            text = html_text(det.get("htmlContent") or det.get("description") or "")
            cats = ", ".join(c["name"] for c in (it.get("categories") or []) + (it.get("styles") or []))
            # Score on title + body only: many posts tick every category, which says nothing.
            blob = (title + " " + text).lower()
            fit = 40
            fit += 4 * min(5, sum(k in blob for k in cfg["filters"]["niche_bonus_keywords"]))
            fit += 12 if any(k in tl for k in ("documentary", "faceless", "true crime", "motion", "animat")) else 0
            fit += 8 if any(k in blob for k in ("after effects", "geolayers", "motion graphics", "animation")) else 0
            fit += 8 if pay != "unlisted" else 0
            boost = pay_boost(pay, text[:600], per)
            if boost <= -15 and jc.get("skip_low_pay", True):
                ignore(jid, title, f"low pay ({pay})")  # e.g. $30 for a shorts edit: not worth a proposal
                continue
            fit += boost  # best-paid jobs get drafted first
            fit += 6 if re.search(r"\b(ai|elevenlabs|ai voice|ai-generated)\b", blob) else 0
            fit += 6 if agency else 0  # agencies hire again and again: steady work
            fit -= 15 if re.search(r"\b(on[- ]camera|shoot days|relocate|in[- ]person|on[- ]site)\b", blob) else 0
            con.execute("""INSERT INTO jobs(id, board, title, company, channel_url, subs, pay, url, posted, text, fit, status, created, updated)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (jid, "YT Jobs", title, comp.get("name"), comp.get("ytLink"),
                         parse_count(str(comp.get("abvSubscribers") or "")), pay, f"https://ytjobs.co/job/{it['id']}",
                         posted, ("[agency] " if agency else "") + (f"[{cats}]\n" if cats else "") + text[:3000],
                         min(fit, 100), "new", now(), now()))
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
                    (key, board, title, company, pay, url, posted, text[:3000],
                     (50 if board == "Bluesky" else 60) + pay_boost(pay, text[:600]), "new", now(), now()))
        extra += 1
    con.commit()
    print(f"other boards: {extra} new editing jobs")
    me = team.me(cfg)
    taken = claimed_elsewhere(cfg)
    con.execute("UPDATE jobs SET status='new' WHERE status='to_draft'")  # last export never drafted: offer again
    ok, why = team.ready(cfg)
    if not ok:
        print("jobs: NOT DRAFTING PROPOSALS TODAY:", why)
    partner_posts = {r[0] for r in con.execute("SELECT job_id FROM leads WHERE job_id IS NOT NULL AND status='partner'")}
    rows = [r for r in con.execute("SELECT * FROM jobs WHERE status='new' ORDER BY fit DESC").fetchall()
            if ok and lead_owner(cfg, r["id"]) == me and not taken.who(r["id"], r["url"])
            and r["id"] not in partner_posts][:cfg["daily"]["jobs_to_draft"]]  # teammates never apply to the same post
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


# Other boards, ONLY through feeds/APIs their owners publish for automated reading (checked 2026-10-10:
# terms, limits, attribution). Each is called once per morning run and the panel links back to the source.
# Sites that ban bots (Indeed, Upwork, LinkedIn, Freelancer, Reddit, Fiverr...) are manual links in the Jobs tab.
JOB_SOURCES = ["Agency hiring pages", "RemoteJobs.org", "Himalayas", "Remote First Jobs", "Remote OK", "Jobicy",
               "Work With Indies", "Open Assignments", "Podnews jobs", "Dribbble", "We Work Remotely",
               "Bluesky hiring posts"]  # Bluesky last: originals win over repost bots
BOARD_TITLE = re.compile(r"video[ -]?edit|motion (design|graphic)|youtube|video (creator|producer|content)|post[- ]production|"
                         r"trailer editor|short[- ]?form|preditor|\banimator\b|2d anim|after effects|reels? editor|"
                         r"tiktok editor|content editor|video specialist|videographer[ /&]+editor", re.I)
BOARD_SKIP = re.compile(r"interpreter|engineer|manager|copy[ -]?editor|editor[- ]in[- ]chief|news editor|managing editor|"
                        r"features editor|photo editor|writer|developer|3d (character )?rigger", re.I)
HIRING_POST = re.compile(r"#hiring|\[hiring\]|\bis hiring\b|^\W*hiring\b|\bwe'?re hiring\b|\bapply\b|\bjob:", re.I | re.M)


def pay_boost(pay, text="", per=None):
    """How well a job pays, as a fit bonus (+20 best .. -15 poor), so the best-paid jobs are drafted first.
    The period comes from the board when it says (YT Jobs jobType, "/hr", "/project"), otherwise from the text,
    otherwise it is guessed from the size."""
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", pay or "")]
    nums = [n for n in nums if n >= 5]
    if not nums:
        return 0
    lo, hi = min(nums), max(nums)
    v = lo * 2 if hi > 20 * lo else (lo + hi) / 2  # "$500-100000" style ranges say nothing about the top
    cur = re.search(r"(?<![A-Z])(EUR|GBP|PHP|INR|CAD|AUD|NZD|SGD|MXN|BRL|PKR|NGN|ZAR)(?![A-Z])|[€£₱₹]", (pay or "").upper())
    if cur:  # rough USD value: a peso salary is not a dollar salary
        v *= {"EUR": 1.08, "€": 1.08, "GBP": 1.27, "£": 1.27, "PHP": 0.017, "₱": 0.017, "INR": 0.012, "₹": 0.012,
              "CAD": 0.73, "AUD": 0.66, "NZD": 0.6, "SGD": 0.74, "MXN": 0.055, "BRL": 0.19, "PKR": 0.0036,
              "NGN": 0.00065, "ZAR": 0.055}.get(cur.group(0), 1)
    t = f"{pay} {text}".lower()
    if not per and re.search(r"/ ?(wk|week)|weekly|per week", t):
        v, per = v * 4.3, "month"  # weekly pay compared as monthly
    elif not per and re.search(r"/ ?day\b|per day|daily rate|day rate", t):
        v, per = v * 21.5, "month"
    if per:
        pass
    elif re.search(r"/ ?(project|video)\b|per (project|video)", t):
        per = "video"
    elif re.search(r"/ ?(hr|hour)|per hour|hourly|an hour", t):
        per = "hour"
    elif re.search(r"/ ?(mo|month)|per month|monthly", t):
        per = "month"
    elif re.search(r"/ ?(yr|year|annual)|per year|annual|salary|/annum", t) or v >= 10000:
        per = "year"
    elif v >= 1200:
        per = "month"
    else:
        per = "video"
    bands = {"hour": [(30, 20), (20, 12), (12, 0)], "month": [(2500, 20), (1500, 12), (800, 0)],
             "year": [(30000, 20), (18000, 12), (9000, 0)], "video": [(500, 18), (250, 10), (100, 2), (60, -5)]}
    for floor, score in bands[per]:
        if v >= floor:
            return score
    return -15


def _rss(board, url, text=True):
    import xml.etree.ElementTree as ET
    out = []
    for it in ET.fromstring(get(url)).findall(".//item"):
        pub = it.findtext("pubDate") or ""
        try:
            pub = dt.datetime.strptime(pub[:16], "%a, %d %b %Y").date().isoformat()
        except ValueError:
            pub = ""
        out.append((board, it.findtext("guid") or it.findtext("link"), it.findtext("title"), "", it.findtext("link"),
                    pub, html_text(it.findtext("description") or "") if text else "", "unlisted"))
    return out


def _himalayas(cfg):
    """Himalayas public API (attribution + link back). locationRestrictions = who may apply; empty = worldwide."""
    out = []
    for q, pages in (("video editor", 3), ("motion designer", 1)):
        for page in range(1, pages + 1):
            ww = "&worldwide=true" if (cfg.get("jobs") or {}).get("worldwide_only", True) else ""
            data = get(f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(q)}&sort=recent&page={page}{ww}", as_json=True)
            for j in data.get("jobs") or []:
                where = j.get("locationRestrictions") or []
                pay = (f"{j.get('currency') or '$'}{j['minSalary']}-{j.get('maxSalary') or ''}/{j.get('salaryPeriod') or ''}"
                       if j.get("minSalary") else "unlisted")
                text = ("Open to: " + (", ".join(where) if where else "worldwide") + "\n" +
                        html_text(j.get("description") or j.get("excerpt") or ""))
                posted = dt.datetime.fromtimestamp(j["pubDate"]).date().isoformat() if j.get("pubDate") else ""
                out.append(("Himalayas", j.get("guid") or j.get("applicationLink"), j.get("title"), j.get("companyName"),
                            j.get("guid") or j.get("applicationLink"), posted, text, pay))
            time.sleep(1.0)
    return out


def _bluesky():
    """Public Bluesky search (read only, never replies or DMs). Repost bots copy other boards: keep one per apply link."""
    out, seen = [], set()
    for q in ('"video editor" hiring', '"motion designer" hiring'):
        data = get("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=" + urllib.parse.quote(q) + "&sort=latest&limit=50",
                   as_json=True)
        for post in data.get("posts") or []:
            rec, author = post.get("record") or {}, post.get("author") or {}
            text = rec.get("text") or ""
            ext = ((post.get("embed") or {}).get("external") or {})
            if rec.get("reply") or not HIRING_POST.search(text + "\n" + (ext.get("description") or "")):
                continue
            link = ext.get("uri") or f"https://bsky.app/profile/{author.get('handle')}/post/{post.get('uri', '').rsplit('/', 1)[-1]}"
            if link in seen:
                continue
            seen.add(link)
            title = (ext.get("title") or text.split("\n")[0])[:140]
            out.append(("Bluesky", post.get("uri"), title, author.get("displayName") or author.get("handle"), link,
                        (rec.get("createdAt") or "")[:10], f"{text}\n{ext.get('description') or ''}", "unlisted"))
        time.sleep(1.0)
    return out


def _remote_first():
    """Remote First Jobs API (link back, don't resubmit elsewhere). Title search is ignored server-side: filter here."""
    out = []
    for page in (0, 1):
        data = get(f"https://remotefirstjobs.com/api/search-jobs?category=design&page={page}", as_json=True)
        for j in data.get("jobs") or []:
            where = j.get("locations") or []
            out.append(("Remote First Jobs", j.get("id"), j.get("title"), j.get("company_name"), j.get("url"),
                        (j.get("published_at") or "")[:10], "Open to: " + (", ".join(where) if where else "worldwide"),
                        f"${j['salary_min']}-{j['salary_max']}" if j.get("salary_min") else "unlisted"))
    return out


WORLDWIDE = re.compile(r"anywhere|worldwide|global|international|\bremote\b(?!.*\b(us|usa|u\.s\.|united states|uk|canada|"
                       r"latam|latin america|europe|eu)\b)", re.I)
COUNTRY_LOCK = re.compile(r"\b(us|usa|u\.s\.|united states|uk|united kingdom|canada|latam|latin america|eu|europe|"
                          r"germany|australia|india|philippines|mexico|brazil)\b(?! and)", re.I)


def _open_to(location):
    """'Open to: worldwide' or 'Open to: <where>' from a free-text location, for the worldwide-only filter."""
    loc = (location or "").strip()
    if not loc or (WORLDWIDE.search(loc) and not COUNTRY_LOCK.search(loc)):
        return "Open to: worldwide"
    return f"Open to: {loc}"


def _pay(lo, hi, period=None, currency="USD"):
    if not (lo or hi):
        return "unlisted"
    sym = "$" if (currency or "USD").upper() == "USD" else (currency or "").upper() + " "
    rng = f"{sym}{int(lo)}-{int(hi)}" if lo and hi else f"{sym}{int(lo or hi)}"
    unit = {"hour": "/hr", "hourly": "/hr", "year": "/yr", "yearly": "/yr", "annual": "/yr", "month": "/mo", "monthly": "/mo",
            "week": "/wk", "project": "/project", "day": "/day"}.get((period or "").lower(), "")
    return rng + unit


def _remotejobs_org():
    """RemoteJobs.org public API (no key; link back to the job, keep its apply link intact; < 200 calls/hour)."""
    out = []
    for q in ("video editor", "motion designer"):
        data = get("https://remotejobs.org/api/v1/jobs?limit=50&q=" + urllib.parse.quote(q), as_json=True)
        for j in data.get("data") or []:
            comp = j.get("company") or {}
            per = j.get("salary_period")
            if per == "week" and j.get("salary_min"):  # weekly pay: compare it as monthly
                lo, hi, per = (j.get("salary_min") or 0) * 4.3, (j.get("salary_max") or 0) * 4.3, "month"
            else:
                lo, hi = j.get("salary_min"), j.get("salary_max")
            out.append(("RemoteJobs.org", j.get("id"), j.get("title"), comp.get("name"), j.get("url") or j.get("apply_url"),
                        (j.get("posted_at") or "")[:10], _open_to(j.get("location")) + "\n" + html_text(j.get("description") or "")[:2500],
                        _pay(lo, hi, per, j.get("salary_currency"))))
        time.sleep(1.0)
    return out


def _ats(cfg):
    """Agencies' own hiring pages on Greenhouse / Lever / Ashby. All three publish a public job-board API for exactly
    this ("Job Board data is publicly available"). The watch-list is config.json -> jobs.ats_boards."""
    out = []
    for b in (cfg.get("jobs") or {}).get("ats_boards") or []:
        kind, board, name = b.get("ats"), b.get("board"), b.get("name") or b.get("board")
        try:
            if kind == "greenhouse":
                for j in get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", as_json=True).get("jobs", []):
                    out.append((f"{name} (agency)", f"gh{j['id']}", j.get("title"), name, j.get("absolute_url"),
                                (j.get("first_published") or j.get("updated_at") or "")[:10],
                                _open_to((j.get("location") or {}).get("name")) + "\n" + html_text(html.unescape(j.get("content") or ""))[:2500],
                                "unlisted"))
            elif kind == "lever":
                for j in get(f"https://api.lever.co/v0/postings/{board}?mode=json", as_json=True):
                    cat = j.get("categories") or {}
                    when = dt.datetime.fromtimestamp((j.get("createdAt") or 0) / 1000).date().isoformat() if j.get("createdAt") else ""
                    out.append((f"{name} (agency)", f"lv{j['id']}", j.get("text"), name, j.get("hostedUrl"), when,
                                _open_to(cat.get("location") or j.get("country")) + "\n" + (j.get("descriptionPlain") or "")[:2500],
                                "unlisted"))
            elif kind == "ashby":
                data = get(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true", as_json=True)
                for j in data.get("jobs") or []:
                    comp = ((j.get("compensation") or {}).get("compensationTierSummary") or "")
                    out.append((f"{name} (agency)", f"as{j['id']}", j.get("title"), name, j.get("jobUrl"),
                                (j.get("publishedAt") or "")[:10],
                                _open_to(j.get("location")) + "\n" + (j.get("descriptionPlain") or "")[:2500], comp or "unlisted"))
            elif kind == "recruitee":  # Recruitee Careers Site API: https://<careers domain>/api/offers/
                host = board if "." in board else f"{board}.recruitee.com"
                for j in get(f"https://{host}/api/offers/", as_json=True).get("offers", []):
                    where = "worldwide" if j.get("remote") and not j.get("country") else ", ".join(
                        x for x in (j.get("city"), j.get("country")) if x) or j.get("location")
                    out.append((f"{name} (agency)", f"rc{j['id']}", j.get("title"), name, j.get("careers_url"),
                                (j.get("created_at") or "")[:10], _open_to(where) + "\n" + html_text(j.get("description") or "")[:2500],
                                "unlisted"))
            elif kind == "teamtailor":  # Teamtailor publishes an RSS feed of open jobs: https://<company>.teamtailor.com/jobs.rss
                host = board if "." in board else f"{board}.teamtailor.com"
                for r in _rss(f"{name} (agency)", f"https://{host}/jobs.rss"):
                    out.append((r[0], "tt" + hashlib.sha1((r[1] or r[4] or "").encode()).hexdigest()[:12], r[2], name, r[4], r[5],
                                _open_to("") + "\n" + (r[6] or "")[:2500] if re.search(r"remote|anywhere|worldwide", r[6] or "", re.I)
                                else "Open to: see post\n" + (r[6] or "")[:2500], "unlisted"))
        except Exception as e:  # one dead board must not stop the others
            print(f"  {name} ({kind}) failed: {type(e).__name__}: {str(e)[:80]}")
        time.sleep(0.5)
    return out


def fetch_other_boards(cfg):
    on = {name: True for name in JOB_SOURCES} | ((cfg.get("jobs") or {}).get("sources") or {})
    calls = {
        "RemoteJobs.org": _remotejobs_org,
        "Agency hiring pages": lambda: _ats(cfg),
        "Podnews jobs": lambda: _rss("Podnews", "https://podnews.net/prebuilt/rss-jobs.xml"),
        "Himalayas": lambda: _himalayas(cfg),
        "Bluesky hiring posts": _bluesky,
        "Remote First Jobs": _remote_first,
        "Remote OK": lambda: [("Remote OK", j.get("id"), j.get("position"), j.get("company"), j.get("url"),
                               (j.get("date") or "")[:10], html_text(j.get("description") or ""),
                               f"${j['salary_min']}-{j['salary_max']}" if j.get("salary_min") else "unlisted")
                              for j in get("https://remoteok.com/api?tags=video", as_json=True)[1:]],  # [0] = legal notice
        "Jobicy": lambda: [("Jobicy", j["id"], j["jobTitle"], j.get("companyName"), j["url"], (j.get("pubDate") or "")[:10],
                            html_text(j.get("jobDescription") or ""), "unlisted")
                           for q in ("tag=video%20editor", "industry=design-multimedia")
                           for j in get(f"https://jobicy.com/api/v2/remote-jobs?count=100&{q}", as_json=True).get("jobs", [])],
        "Work With Indies": lambda: _rss("Work With Indies", "https://workwithindies.com/careers/rss.xml"),
        "Open Assignments": lambda: [r for r in _rss("Open Assignments", "https://openassignments.com/feed.xml")
                                     if re.search(r"remote", (r[2] or "") + " " + (r[6] or ""), re.I)],
        "Dribbble": lambda: _rss("Dribbble", "https://dribbble.com/jobs.rss"),
        # WWR's API terms forbid storing their data: keep only the title + link, never the description
        "We Work Remotely": lambda: _rss("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss", text=False),
    }
    out = []
    for name in JOB_SOURCES:
        if not on.get(name):
            continue
        try:
            got = calls[name]()
            out += got
        except Exception as e:
            print(f"{name} failed: {type(e).__name__}: {str(e)[:120]}")
    max_age = min(45, (cfg.get("jobs") or {}).get("max_age_days", 30))
    jc = cfg.get("jobs") or {}
    skip_words = [w.lower() for w in jc.get("exclude", [])]
    worldwide = jc.get("worldwide_only", True)
    keep, seen_urls = [], set()
    for board, jid, title, company, url, posted, text, pay in out:
        if not title or not jid or not BOARD_TITLE.search(title) or BOARD_SKIP.search(title):
            continue
        if any(w in title.lower() for w in skip_words) or url in seen_urls:
            continue
        if worldwide and (text or "").startswith("Open to: ") and not text.startswith("Open to: worldwide"):
            continue  # locked to certain countries: international, open-to-anyone roles only
        if jc.get("skip_low_pay", True) and pay_boost(pay, (text or "")[:600]) <= -15:
            continue
        try:  # agencies keep "always hiring" posts open for months: their own hiring pages skip the age check
            if posted and not board.endswith("(agency)") and (dt.date.today() - dt.date.fromisoformat(posted)).days > max_age:
                continue
        except ValueError:
            pass
        seen_urls.add(url)
        keep.append((board, str(jid), title.strip(), company or "", url, posted, text or "", pay))
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
    history = []
    for i in range(13, -1, -1):  # last 14 days, oldest first, for the Stats chart
        d = add_days(-i, day)
        row = dict(con.execute(f"SELECT type, COUNT(*) FROM events WHERE day=? {acc_clause} GROUP BY type", [d] + params).fetchall())
        history.append({"day": d, "sent": row.get("sent", 0), "replied": row.get("replied", 0), "applied": row.get("applied", 0)})
    by_platform = {p: {t: con.execute(f"SELECT COUNT(*) FROM events WHERE type=? AND platform=? {acc_clause}",
                                      [t, p] + params).fetchone()[0] for t in ("sent", "replied", "won")} for p in PLATFORMS}
    applications = dict(con.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall())
    return {"day": day, "sent_today": sent, "queued_due": queued, "week": week, "total": total,
            "leads": leads, "jobs": jobs, "applied_today": applied_today, "history": history,
            "by_platform": by_platform, "applications": applications,
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
    import inbox
    cfg = load_config()
    # Low-DM mode with the email lane off: cold channels are never messaged, so don't spend 10 minutes finding them
    cold = cfg.get("dm_mode", "all") != "warm" or effective_caps(cfg, "me").get("email", 0) > 0
    steps = [("server", lambda: print("server:", ensure_server())),
             ("team", lambda: team.sync(db())),  # before drafting: newest member list + teammates' claims
             ("inbox", lambda: inbox.run(db(), load_config(), days=3)),  # job-alert emails -> jobs (before the export)
             ("jobs", lambda: cmd_jobs(argparse.Namespace(full=False))),
             ("job-leads", lambda: cmd_job_leads(argparse.Namespace(limit=60))),
             ("discover", (lambda: cmd_discover(argparse.Namespace(niches=None))) if cold else (lambda: print("skipped: low-DM mode"))),
             ("enrich", (lambda: cmd_enrich(argparse.Namespace(limit=None))) if cold else (lambda: print("skipped: low-DM mode"))),
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
    sub.add_parser("report"); sub.add_parser("status"); sub.add_parser("daily")
    s = sub.add_parser("team-sync"); s.add_argument("--take-over", action="store_true",
                                                    help="this PC replaces the old PC registered under your name")
    s = sub.add_parser("team-join"); s.add_argument("name")
    s = sub.add_parser("check"); s.add_argument("query", help="a profile / job link, @handle or email")
    s = sub.add_parser("inbox"); s.add_argument("--days", type=int, default=3); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("finish"); s.add_argument("file", nargs="?", default=str(WORK / "drafts.json")); s.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"add-channels": cmd_add_channels, "discover": cmd_discover, "enrich": cmd_enrich, "to-draft": cmd_to_draft,
     "import-drafts": cmd_import_drafts, "jobs": cmd_jobs, "job-leads": cmd_job_leads, "report": cmd_report, "status": cmd_status,
     "daily": cmd_daily, "finish": cmd_finish,
     "team-sync": lambda a: team.sync(db(), take_over=a.take_over), "team-join": lambda a: team.join(db(), a.name),
     "check": lambda a: print(json.dumps(team.check(db(), load_config(), a.query), indent=1)),
     "inbox": lambda a: __import__("inbox").run(db(), load_config(), days=a.days, dry=a.dry_run)}[a.cmd](a)


if __name__ == "__main__":
    main()
