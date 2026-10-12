"""Job-alert inbox: turn the job-alert EMAILS that Indeed, LinkedIn, Upwork, Glassdoor, OnlineJobs.ph... send you
into jobs in the queue, so the morning run can draft a proposal for each one.

Why email: those sites forbid bots and scraping, but they happily email you new jobs for a saved search. Reading
your own mailbox touches none of their sites. Nothing here ever opens a job site: links are stored as they are and
you click them yourself. Emails are read with BODY.PEEK, so nothing is marked read, moved or deleted.

The mailbox login lives OUTSIDE this folder (%APPDATA%/Outreach/inbox.json), and the password is encrypted with
Windows DPAPI (only this Windows user can decrypt it). It is never in config.json, git or the brain sync.
Use an app password (Gmail: 2-Step Verification on, then myaccount.google.com/apppasswords), not your real one.

    python engine.py inbox [--days 3] [--dry-run]
"""
import base64
import datetime as dt
import email
import email.policy
import email.utils
import hashlib
import html
import imaplib
import json
import os
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

from core import now

SECRET = Path(os.environ.get("APPDATA") or Path.home()) / "Outreach" / "inbox.json"
HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com", "outlook.com": "outlook.office365.com",
         "hotmail.com": "outlook.office365.com", "live.com": "outlook.office365.com", "yahoo.com": "imap.mail.yahoo.com",
         "icloud.com": "imap.mail.me.com", "proton.me": "127.0.0.1"}
# sender domain -> board name. Only mail FROM these domains is ever parsed.
BOARDS = {"indeed.com": "Indeed", "indeedemail.com": "Indeed", "indeedmail.com": "Indeed",
          "linkedin.com": "LinkedIn", "upwork.com": "Upwork", "glassdoor.com": "Glassdoor",
          "onlinejobs.ph": "OnlineJobs.ph", "ytjobs.co": "YT Jobs", "himalayas.app": "Himalayas", "contra.com": "Contra",
          "wellfound.com": "Wellfound", "freelancer.com": "Freelancer", "peopleperhour.com": "PeoplePerHour",
          "workana.com": "Workana", "jobgether.com": "Jobgether", "remoterocketship.com": "Remote Rocketship",
          "weworkremotely.com": "We Work Remotely", "remoteok.com": "Remote OK", "dribbble.com": "Dribbble",
          "jobicy.com": "Jobicy", "ziprecruiter.com": "ZipRecruiter", "behance.net": "Behance", "mandy.com": "Mandy",
          "dailyremote.com": "DailyRemote", "dynamitejobs.com": "Dynamite Jobs", "twine.net": "Twine",
          "hubstafftalent.net": "Hubstaff Talent", "motionographer.com": "Motionographer", "remotejobs.org": "RemoteJobs.org",
          "stashmedia.tv": "Stash Jobs"}
# the part of a job link that identifies the post, per board (searched in the link and in its decoded redirect target)
JOB_ID = [
    ("Indeed", re.compile(r"[?&]jk=([0-9a-f]{16})", re.I), "https://www.indeed.com/viewjob?jk={}"),
    ("LinkedIn", re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})"), "https://www.linkedin.com/jobs/view/{}/"),
    ("LinkedIn", re.compile(r"[?&]currentJobId=(\d{6,})"), "https://www.linkedin.com/jobs/view/{}/"),
    ("Upwork", re.compile(r"(~0[0-9a-z]{15,22})", re.I), "https://www.upwork.com/jobs/{}"),
    ("Glassdoor", re.compile(r"[?&](?:jobListingId|jl)=(\d+)"), "https://www.glassdoor.com/job-listing/j?jl={}"),
    ("YT Jobs", re.compile(r"ytjobs\.co/job/(\d+)"), "https://ytjobs.co/job/{}"),
    ("OnlineJobs.ph", re.compile(r"onlinejobs\.ph/jobseekers/job/[^?#\s\"']*?-?(\d{4,})"), None),
    ("Wellfound", re.compile(r"wellfound\.com/jobs/(\d+)"), None),
    ("PeoplePerHour", re.compile(r"peopleperhour\.com/freelance-jobs/[^?#\s\"']*?-(\d{5,})"), None),
    ("Freelancer", re.compile(r"freelancer\.com/projects/([a-z0-9\-]+/[a-z0-9\-]+)", re.I), None),
    ("Himalayas", re.compile(r"(himalayas\.app/companies/[^/?#\s\"']+/jobs/[^/?#\s\"']+)"), None),
    ("Contra", re.compile(r"contra\.com/opportunity/([^/?#\s\"']+)"), None),
    ("DailyRemote", re.compile(r"dailyremote\.com/remote-job/([^/?#\s\"']+)"), None),
    ("Dynamite Jobs", re.compile(r"dynamitejobs\.com/company/[^/?#\s\"']+/remote-job/([^/?#\s\"']+)"), None),
    ("Twine", re.compile(r"twine\.net/jobs?/[^?#\s\"']*?(\d{4,})"), None),
]
# boards without a known link shape: a link back to the sender's own site whose path looks like a single job
GENERIC_JOB_PATH = re.compile(r"/(jobs?|remote-jobs?|careers?|positions?|opportunit(y|ies)|projects?)/[^?#\s\"']{4,}", re.I)
GENERIC = re.compile(r"^\s*(view( job| details| all| more)?|apply( now)?|see (job|more|all).*|details|learn more|"
                     r"unsubscribe|manage.*|settings|privacy.*|help|here|easy apply|save|share|new)\s*$", re.I)
JOBISH = re.compile(r"edit|motion|animat|video|youtube|reel|short|tiktok|content|post[- ]?production|vfx|after effects|"
                    r"premiere|davinci|thumbnail|creator|visual|graphic", re.I)


# ---------------------------------------------------------------- secrets (DPAPI on Windows)

def _dpapi(data, protect=True):
    if sys.platform != "win32":
        return data  # elsewhere the file is only readable by its owner (chmod 600)
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not " + ("encrypt" if protect else "decrypt") + " the mailbox password")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def save_login(user, password, host=None, folder="INBOX"):
    user = (user or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", user):
        raise ValueError("that doesn't look like an email address")
    host = (host or "").strip() or HOSTS.get(user.split("@")[1].lower(), "imap." + user.split("@")[1].lower())
    old = load_login(with_password=True) or {}
    pw = (password or "").replace(" ", "") or old.get("password")  # Gmail shows app passwords in groups of 4
    if not pw:
        raise ValueError("enter the app password")
    SECRET.parent.mkdir(parents=True, exist_ok=True)
    blob = {"user": user, "host": host, "folder": folder or "INBOX",
            "password": base64.b64encode(_dpapi(pw.encode("utf-8"))).decode("ascii"), "saved": now()}
    tmp = SECRET.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob), encoding="utf-8")
    tmp.replace(SECRET)
    if sys.platform != "win32":
        os.chmod(SECRET, 0o600)
    return {"user": user, "host": host}


def load_login(with_password=False):
    try:
        d = json.loads(SECRET.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if with_password:
        d["password"] = _dpapi(base64.b64decode(d["password"]), protect=False).decode("utf-8")
    else:
        d.pop("password", None)
    return d


def forget_login():
    SECRET.unlink(missing_ok=True)


def masked(user):
    name, _, dom = (user or "").partition("@")
    return (name[:2] + "•••@" + dom) if dom else ""


# ---------------------------------------------------------------- parsing one alert email

class _Tokens(HTMLParser):
    """Flatten an HTML email into a list of text runs, each with the link it sits in (or None)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.href, self.skip = [], None, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        if tag == "a":
            self.href = dict(attrs).get("href")
        if tag in ("br", "p", "tr", "td", "div", "li", "h1", "h2", "h3", "h4", "table"):
            self.out.append(("\n", None))

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
        if tag == "a":
            self.href = None

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append((re.sub(r"\s+", " ", data).strip(), self.href))


def _indeed_cts(u):
    """Indeed's cts.indeed.com/v3/<token> links: base64url -> gzip -> {"u": destination, "m": {"clickType": ...}}.
    Decoded offline (never fetched). Only job links count; feedback / settings / opt-out links return None."""
    import gzip
    m = re.match(r"https?://cts\.indeed\.com/v3/([A-Za-z0-9_\-]+)", u)
    if not m:
        return None
    tok = m.group(1)
    try:
        data = json.loads(gzip.decompress(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))))
    except (ValueError, OSError, EOFError):
        return None
    kind = ((data.get("m") or {}).get("clickType") or "").lower()
    return data.get("u") if kind in ("viewjob", "apply") and isinstance(data.get("u"), str) else ""


def _targets(href):
    """The link and any URL hidden inside it (click-tracking redirects carry the real link as a parameter).
    Only used to READ the job id: tracked links (which carry personal tokens) are never stored or opened."""
    seen, todo = [], [href]
    while todo and len(seen) < 6:
        u = html.unescape(todo.pop())
        if u in seen:
            continue
        cts = _indeed_cts(u)
        if cts is not None:  # an Indeed tracking link: its destination, or nothing if it isn't a job link
            if cts:
                todo.append(cts)
            seen.append(u)
            continue
        seen.append(u)
        for _, v in urllib.parse.parse_qsl(urllib.parse.urlparse(u).query):
            if v.startswith("http"):
                todo.append(v)
        d = urllib.parse.unquote(u)
        if d != u:
            todo.append(d)
    return seen


def _untracked(url):
    """The link without click-tracking parameters (utm_*, ref, source...) or a fragment."""
    u = urllib.parse.urlparse(url.split("#")[0])
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(u.query)
            if not re.match(r"(?i)^(utm_.*|ref.*|source|src|from|trk.*|tracking.*|mc_[ce]id|fbclid|gclid|e?mid|cid|tk|bb)$", k)]
    return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(keep)))


def job_key(board, href):
    """(stable id, clean link) for a job link, or None if it isn't one we can recognise."""
    targets = _targets(href)
    for name, rx, canon in JOB_ID:
        for t in targets:
            m = rx.search(t)
            if m:  # rebuild the link only where its form is known; otherwise keep the real (untracked) target
                slug = name.lower().replace(" ", "").replace(".", "")
                clean = canon.format(m.group(1)) if canon else _untracked(t)
                return f"{slug}:{m.group(1).lower() if name != 'Himalayas' else m.group(1)}", clean
    domains = [d for d, n in BOARDS.items() if n == board]
    for t in targets:  # unknown link shape: a page on the sender's own site that looks like one job
        u = urllib.parse.urlparse(t)
        host = (u.hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in domains) and GENERIC_JOB_PATH.search(u.path):
            slug = board.lower().replace(" ", "").replace(".", "")
            return f"{slug}:{hashlib.sha1((host + u.path).encode()).hexdigest()[:12]}", f"https://{host}{u.path}"
    return None


def parse_alert(board, subject, body_html, body_text=""):
    """List of {key, title, company, url, context} from one alert email. Pure function (tested in selftest)."""
    if body_html:
        p = _Tokens()
        p.feed(body_html)
        toks = p.out
    else:  # plain-text alert: "Title\nCompany\nhttps://..." blocks
        toks = []
        for line in (body_text or "").splitlines():
            line = line.strip()
            if line:
                m = re.search(r"https?://\S+", line)
                toks.append((line, m.group(0) if m else None) if not m or line != m.group(0) else ("View job", m.group(0)))
    jobs, seen = [], set()
    for i, (text, href) in enumerate(toks):
        if not href or not href.startswith("http"):
            continue
        k = job_key(board, href)
        if not k or k[0] in seen:
            continue
        title = text if (len(text) >= 6 and not GENERIC.match(text)) else ""
        if not title:  # "View job" button: the title is the closest real text before it
            for back in range(i - 1, max(-1, i - 8), -1):
                t = toks[back][0]
                if t != "\n" and len(t) >= 6 and not GENERIC.match(t):
                    title = t
                    break
        after = [t for t, _ in toks[i + 1:i + 14] if t != "\n" and not GENERIC.match(t) and len(t) > 1][:6]
        m = re.match(r"\s*new job:\s*(.+)", subject or "", re.I)  # Upwork sends one job per email: "New job: <title>"
        if m and (not title or not JOBISH.search(title)):
            title = m.group(1).strip()
        if not title or not JOBISH.search(title):
            continue
        seen.add(k[0])
        jobs.append({"key": k[0], "url": k[1], "title": title[:200], "company": (after[0] if after else "")[:120],
                     "context": " · ".join(after)[:600], "board": board})
    return jobs


# ---------------------------------------------------------------- reading the mailbox

def _board_of(sender):
    dom = (email.utils.parseaddr(sender or "")[1].split("@")[-1] or "").lower()
    for d, name in BOARDS.items():
        if dom == d or dom.endswith("." + d):
            return name
    return None


def _connect():
    login = load_login(with_password=True)
    if not login:
        raise RuntimeError("job-alert inbox not set up (Jobs → Alerts in the queue page)")
    m = imaplib.IMAP4_SSL(login["host"], 993, timeout=40)
    try:
        m.login(login["user"], login["password"])
    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"mailbox login failed ({e}). Use an app password, and turn IMAP on in your mail settings.") from None
    typ, _ = m.select(f'"{login.get("folder") or "INBOX"}"', readonly=True)
    if typ != "OK":
        raise RuntimeError(f"folder {login.get('folder')} not found")
    return m


def _alerts(m, days):
    """Yield (board, subject, date, message) for alert emails from known job boards in the last `days` days."""
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    typ, data = m.search(None, f"(SINCE {since})")
    ids = data[0].split() if typ == "OK" and data and data[0] else []
    for start in range(0, len(ids), 200):
        chunk = b",".join(ids[start:start + 200])
        typ, rows = m.fetch(chunk, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
        for row in rows or []:
            if not isinstance(row, tuple):
                continue
            hdr = email.message_from_bytes(row[1], policy=email.policy.default)
            board = _board_of(str(hdr.get("From", "")))
            if not board:
                continue
            num = row[0].split()[0]
            typ, full = m.fetch(num, "(BODY.PEEK[])")
            raw = next((x[1] for x in full or [] if isinstance(x, tuple)), None)
            if raw:
                yield board, str(hdr.get("Subject", "")), str(hdr.get("Date", "")), str(hdr.get("Message-ID", "")), \
                    email.message_from_bytes(raw, policy=email.policy.default)


def _bodies(msg):
    h = t = ""
    try:
        part = msg.get_body(preferencelist=("html",))
        h = part.get_content() if part else ""
    except (KeyError, LookupError):
        pass
    try:
        part = msg.get_body(preferencelist=("plain",))
        t = part.get_content() if part else ""
    except (KeyError, LookupError):
        pass
    return h, t


def test_login():
    """Connect and count alert emails per board over the last 14 days. Nothing is stored."""
    m = _connect()
    try:
        counts = {}
        for board, *_ in _alerts(m, 14):
            counts[board] = counts.get(board, 0) + 1
        return counts
    finally:
        try:
            m.logout()
        except Exception:
            pass


def run(con, cfg, days=3, dry=False):
    """Read recent alert emails and add the jobs they list. Returns a summary dict."""
    from engine import BOARD_SKIP  # same "not an editing job" words as the feeds
    if not load_login():
        print("inbox: not set up (open the queue page → Jobs → Alerts). Skipped.")
        return {"skipped": "not set up"}
    con.execute("CREATE TABLE IF NOT EXISTS inbox_seen (msgid TEXT PRIMARY KEY, board TEXT, day TEXT, jobs INTEGER)")
    skip_words = [w.lower() for w in (cfg.get("jobs") or {}).get("exclude", [])]
    m = _connect()
    added, emails, per_board = 0, 0, {}
    try:
        for board, subject, date, msgid, msg in _alerts(m, days):
            key = msgid or hashlib.sha1((board + subject + date).encode()).hexdigest()
            if not dry and con.execute("SELECT 1 FROM inbox_seen WHERE msgid=?", (key,)).fetchone():
                continue
            emails += 1
            h, t = _bodies(msg)
            try:
                day = email.utils.parsedate_to_datetime(date).date().isoformat()
            except (TypeError, ValueError):
                day = dt.date.today().isoformat()
            found = [j for j in parse_alert(board, subject, h, t)
                     if not BOARD_SKIP.search(j["title"]) and not any(w in j["title"].lower() for w in skip_words)]
            n = 0
            for j in found:
                if dry:
                    print(f"  [{board}] {j['title'][:70]} | {j['company'][:30]} | {j['url'][:70]}")
                    n += 1
                    continue
                if con.execute("SELECT 1 FROM jobs WHERE id=? OR url=?", (j["key"], j["url"])).fetchone():
                    continue
                text = f"From your {board} job alert ({subject[:120]}, {day}).\n{j['context']}"
                con.execute("""INSERT INTO jobs(id, board, title, company, pay, url, posted, text, fit, status, created, updated)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (j["key"], board, j["title"], j["company"], "unlisted", j["url"], day, text, 65, "new", now(), now()))
                n += 1
            per_board[board] = per_board.get(board, 0) + n
            added += n
            if not dry:
                con.execute("INSERT OR REPLACE INTO inbox_seen(msgid, board, day, jobs) VALUES (?,?,?,?)", (key, board, day, n))
                con.commit()
    finally:
        try:
            m.logout()
        except Exception:
            pass
    print(f"inbox: {emails} new alert emails, {added} {'jobs found' if dry else 'new jobs added'} {per_board}")
    return {"emails": emails, "added": added, "per_board": per_board}
