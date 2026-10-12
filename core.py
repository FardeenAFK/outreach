"""Shared paths, config, database and date helpers for the Outreach system."""
import json
import os
import sqlite3
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DB_PATH = Path(os.environ.get("OUTREACH_DB") or ROOT / "outreach.db")  # env override for tests
WORK = ROOT / "work"
REPORTS = ROOT / "reports"
WORK.mkdir(exist_ok=True)
REPORTS.mkdir(exist_ok=True)

PLATFORMS = ("x", "instagram", "linkedin", "facebook", "email")
# Hard daily ceilings from the 2026-09-27 research (aged accounts, every message unique).
# The settings UI cannot go above these. Email = hand-sent from your own mailbox, so it stays low.
MAX_CAPS = {"x": 40, "instagram": 30, "linkedin": 15, "facebook": 20, "email": 15}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,            -- YouTube channel id (UC...)
    source TEXT,
    name TEXT,
    url TEXT,
    subs INTEGER,
    country TEXT,
    description TEXT,
    links TEXT,                     -- JSON {x, instagram, linkedin, tiktok, website[]}
    email TEXT,
    has_business_email INTEGER DEFAULT 0,
    videos TEXT,                    -- JSON list of recent uploads
    last_upload_days INTEGER,
    uploads_90d INTEGER,
    median_views INTEGER,
    signals TEXT,                   -- JSON list of human-readable reasons
    score REAL DEFAULT 0,
    platform TEXT,                  -- chosen outreach platform
    status TEXT DEFAULT 'new',      -- new|enriched|filtered|to_draft|queued|contacted|replied|won|dead|error
    note TEXT,
    created TEXT,
    updated TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT,
    account TEXT,
    platform TEXT,
    profile_url TEXT,
    step INTEGER,                   -- 0 = first touch, 1.. = follow-ups
    body TEXT,
    status TEXT,                    -- queued|waiting|sent|skipped|cancelled
    due_date TEXT,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,            -- "ytjobs:<id>"
    board TEXT,
    title TEXT,
    company TEXT,
    channel_url TEXT,
    subs INTEGER,
    pay TEXT,
    url TEXT,
    posted TEXT,
    text TEXT,
    fit REAL,
    proposal TEXT,
    status TEXT DEFAULT 'new',      -- new|to_draft|drafted|applied|skipped
    created TEXT,
    updated TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    day TEXT,
    type TEXT,                      -- sent|skipped|replied|won|dead|applied|job_skipped
    account TEXT,
    platform TEXT,
    lead_id TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- jobs applied to by hand (Indeed, Upwork, LinkedIn...) or from a proposal
    board TEXT,
    title TEXT,
    company TEXT,
    url TEXT,
    job_id TEXT,                    -- set when it came from a drafted proposal
    status TEXT DEFAULT 'applied',  -- applied|interview|won|lost|ghosted
    notes TEXT,
    followup_due TEXT,
    created TEXT,
    updated TEXT
);
CREATE INDEX IF NOT EXISTS ix_msg_status ON messages(status, account, platform);
CREATE INDEX IF NOT EXISTS ix_ev_day ON events(day, type);
"""


def _retry(fn, tries=20):
    import time
    for i in range(tries):
        try:
            return fn()
        except PermissionError:  # Windows: another process is mid-replace
            if i == tries - 1:
                raise
            time.sleep(0.05)


def load_config():
    return _retry(lambda: json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def save_config(cfg):
    import threading
    tmp = CONFIG_PATH.with_name(f".config.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    _retry(lambda: tmp.replace(CONFIG_PATH))


def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(leads)")}
    if "job_id" not in cols:  # migration: leads that came from a job post
        con.execute("ALTER TABLE leads ADD COLUMN job_id TEXT")
    if "dm_checked" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:  # migration: last no-socials check
        con.execute("ALTER TABLE jobs ADD COLUMN dm_checked TEXT")
    if "subject" not in {r[1] for r in con.execute("PRAGMA table_info(messages)")}:  # migration: email subject lines
        con.execute("ALTER TABLE messages ADD COLUMN subject TEXT")
    if "spec_idea" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:  # migration: today's spec-clip idea
        con.execute("ALTER TABLE jobs ADD COLUMN spec_idea TEXT")
    return con


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def today():
    return dt.date.today().isoformat()


def add_days(n, base=None):
    base = dt.date.fromisoformat(base) if base else dt.date.today()
    return (base + dt.timedelta(days=n)).isoformat()


def ramp_factor(cfg, day=None):
    """Warm-up: 50% of caps in week 1, 75% in week 2, full after."""
    r = cfg.get("ramp") or {}
    try:
        start = dt.date.fromisoformat(r["start_date"])
    except (KeyError, ValueError):
        return r.get("week1_pct", 50) / 100  # missing/unfilled start date: fail safe to the slowest ramp
    d = dt.date.fromisoformat(day) if day else dt.date.today()
    days = (d - start).days
    if days < 7:
        return r.get("week1_pct", 50) / 100
    if days < 14:
        return r.get("week2_pct", 75) / 100
    return 1.0


def effective_caps(cfg, account):
    caps = (cfg["accounts"].get(account) or {}).get("caps", {})
    f = ramp_factor(cfg)
    return {p: max(1, int(round(caps.get(p, 0) * f))) if caps.get(p, 0) else 0 for p in PLATFORMS}


def log_event(con, type_, account=None, platform=None, lead_id=None, detail=None):
    con.execute(
        "INSERT INTO events(ts, day, type, account, platform, lead_id, detail) VALUES (?,?,?,?,?,?,?)",
        (now(), today(), type_, account, platform, lead_id, detail),
    )
