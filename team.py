"""Team split: every lead and job post belongs to exactly one teammate, the same answer on every PC.

The member list and each member's claims live on the repo's `team` branch, apart from the code on main:
    members.json          the team, in join order (names are only ever APPENDED)
    claimed/<name>.txt    hashes of every lead, social profile, email, job post and application that member has
                          queued, contacted or applied to, so nobody else ever pitches that person
    installs/<name>.txt   which PC uses that name, so two PCs can never both be "sami"
`sync()` fetches the branch, keeps the member list in config.json -> team.members and the claims in
work/team-claims.json, then publishes this PC's claims. A git checkout reads and writes; a folder without git
(downloaded as a ZIP) can only read, through the GitHub API. Offline, the last synced copy is used.

Removing or reordering a member name reshuffles who owns what. Someone who stops keeps their place in the list.

    python engine.py team-sync [--take-over]   # fetch members + claims, publish mine (daily, finish, server do it too)
    python engine.py team-join NAME            # add NAME to the team and make this PC that person
    python engine.py check URL_OR_HANDLE       # is this person free, mine, or a teammate's?
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from core import ROOT, WORK, load_config, save_config, now

BRANCH = "team"
REMOTE_REF = f"refs/remotes/origin/{BRANCH}"
REPO_DEFAULT = "FardeenAFK/outreach"
CLAIMS_CACHE = WORK / "team-claims.json"
STATUS_FILE = WORK / "team-status.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,23}$")
SOCIAL_KEYS = ("x", "instagram", "linkedin", "facebook", "tiktok")
CLAIMED_LEAD = ("to_draft", "queued", "contacted", "replied", "won", "dead")
CLAIMED_JOB = ("to_draft", "drafted", "applied")  # not "dm": the DM lead itself is claimed only by the PC that queues it


# ---------------------------------------------------------------- who owns a lead

def _jump(key, n):
    """Jump consistent hash (Lamping & Veach): going from n to n+1 buckets only moves keys INTO the new bucket."""
    b, j = -1, 0
    while j < n:
        b = j
        key = (key * 2862933555777941757 + 1) & 0xFFFFFFFFFFFFFFFF
        j = int((b + 1) * (float(1 << 31) / float((key >> 33) + 1)))
    return b


def owner(members, lead_id):
    """The first two members keep the original sha1 % 2 split, so leads already divided between them never move.
    Every later member takes an even share, and adding one only moves leads to the new person."""
    if not members:
        return None
    if len(members) < 2:
        return members[0]
    h = hashlib.sha1(lead_id.encode()).hexdigest()
    j = _jump(int(hashlib.sha1(b"jump:" + lead_id.encode()).hexdigest()[:16], 16), len(members))
    return members[j] if j >= 2 else members[int(h, 16) % 2]


def me(cfg):
    return (cfg.get("team") or {}).get("me", "me")


def members(cfg):
    """The synced member list. No list = no drafting, unless config says team.solo (someone working alone):
    a fresh install that never synced must not treat every client as its own."""
    t = cfg.get("team") or {}
    if t.get("members"):
        return t["members"]
    return [me(cfg)] if t.get("solo") else []


def status():
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ready(cfg):
    """(ok, why): may this PC draft right now? Drafting is refused rather than risking a double pitch."""
    t = cfg.get("team") or {}
    if t.get("solo"):
        return True, ""
    st = status()
    if st.get("conflict") and st.get("me") == me(cfg):
        return False, st["conflict"]
    mem = members(cfg)
    if not mem:
        return False, ("No team list yet. Run `python engine.py team-sync` (needs internet), or set "
                       "\"solo\": true under team in config.json if you work alone.")
    if me(cfg) not in mem:
        return False, f"'{me(cfg)}' is not in the team list {mem}. Run `python engine.py team-join {me(cfg)}`."
    return True, ""


# ---------------------------------------------------------------- identities and claims

def claim_key(value):
    return hashlib.sha1(b"claim:" + value.encode()).hexdigest()[:16]


PROFILE_HOSTS = ("x.com", "instagram.com", "linkedin.com", "tiktok.com", "facebook.com")
TRACKING = re.compile(r"^(utm_.*|ref|ref_src|refid|referrer.*|fbclid|gclid|si|igsh|igshid|trk|trackingid|from|src|source|s|t)$", re.I)


def norm_link(u):
    """One spelling per person or post: x.com == twitter.com, no www/m., no trailing slash or tracking parameters.
    Profiles drop the whole query; job links keep the parts that identify the post (indeed.com/viewjob?jk=...)."""
    u = (u or "").strip()
    if not u:
        return ""
    if u.lower().startswith("mailto:"):
        return "e:" + u[7:].split("?")[0].strip().lower()
    if "@" in u and "/" not in u and " " not in u and not u.startswith("@"):
        return "e:" + u.lower()
    if u.startswith("@"):  # a bare handle: can't know the platform, match it on all of them
        return "h:" + u[1:].lower()
    if "//" not in u:
        u = "https://" + u
    try:
        p = urllib.parse.urlparse(u)
    except ValueError:
        return ""
    raw = (p.hostname or "").lower()
    path = re.sub(r"/+$", "", p.path)
    if raw == "m.me":  # Messenger link = that Facebook profile
        raw, path = "facebook.com", path
    if raw == "ig.me" and path.lower().startswith("/m/"):
        raw, path = "instagram.com", path[2:]
    if raw == "youtu.be" and path:
        raw, path, p = "youtube.com", "/watch", p._replace(query="v=" + path.strip("/"))
    host = re.sub(r"^(www|m|mobile|web)\.", "", raw)
    host = {"twitter.com": "x.com", "fb.com": "facebook.com"}.get(host, host)
    if host in PROFILE_HOSTS or host.endswith(".linkedin.com"):
        path = path.lower()
        q = "?" + p.query if host == "facebook.com" and path == "/profile.php" and p.query else ""
    else:
        keep = sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query) if not TRACKING.match(k))
        q = ("?" + urllib.parse.urlencode(keep)) if keep else ""
        path = path.lower() if "youtube.com" in host else path
    return f"p:{host}{path}{q}" if host else ""


def keys_for(*values):
    """Claim keys for ids (lead/job ids as given) and links/emails/handles (normalised)."""
    out = set()
    for v in values:
        if not v or not isinstance(v, str):
            continue
        v = v.strip()
        out.add(claim_key(v))
        n = norm_link(v) if ("/" in v or "@" in v or "." in v) else ""
        if n:
            out.add(claim_key(n))
            if n.startswith("p:") and n.split("/")[0][2:] in ("x.com", "instagram.com", "tiktok.com"):
                handle = n.split("/", 1)[1] if "/" in n else ""
                if handle and "/" not in handle:
                    out.add(claim_key("h:" + handle.lstrip("@")))  # so "@handle" typed in Check matches too
    return out


def lead_idents(lead):
    """Everything that identifies the person behind a lead row."""
    links = json.loads(lead["links"] or "{}") if lead["links"] else {}
    vals = [lead["id"], lead["url"], lead["email"]]
    try:
        vals.append(lead["job_id"])
    except (IndexError, KeyError):
        pass
    vals += [links.get(k) for k in SOCIAL_KEYS]
    return [v for v in vals if isinstance(v, str) and v]


class Taken:
    """`who(*idents)` -> the teammate who already claimed this person, or None. `x in taken` works for one value."""

    def __init__(self, cfg):
        mine = me(cfg)
        try:
            claims = json.loads(CLAIMS_CACHE.read_text(encoding="utf-8")) if CLAIMS_CACHE.exists() else {}
        except (OSError, ValueError):
            claims = {}
        self.owner_of = {}
        for name, ks in claims.items():
            if name != mine:
                for k in ks:
                    self.owner_of.setdefault(k, name)
        legacy = ROOT / "claimed_elsewhere.json"  # the old zip package shipped raw ids
        self.ids = set(json.loads(legacy.read_text(encoding="utf-8"))) if legacy.exists() else set()

    def who(self, *idents):
        for v in idents:
            if v in self.ids:
                return "teammate"
        for k in keys_for(*idents):
            if k in self.owner_of:
                return self.owner_of[k]
        return None

    def __contains__(self, value):
        return self.who(value) is not None


def my_claims(con):
    """Every person and post this PC has queued, contacted or applied to, as sorted hashes."""
    vals = []
    leads = con.execute(f"""SELECT * FROM leads WHERE status IN ({','.join('?' * len(CLAIMED_LEAD))})
                            OR id IN (SELECT DISTINCT lead_id FROM messages)""", CLAIMED_LEAD).fetchall()
    for l in leads:
        vals += lead_idents(l)
    vals += [r[0] for r in con.execute("SELECT DISTINCT profile_url FROM messages WHERE profile_url IS NOT NULL")]
    for j in con.execute(f"SELECT id, url FROM jobs WHERE status IN ({','.join('?' * len(CLAIMED_JOB))})", CLAIMED_JOB):
        vals += [j["id"], j["url"]]
    vals += [r[0] for r in con.execute("SELECT url FROM applications WHERE url IS NOT NULL AND url != ''")]
    return "\n".join(sorted(keys_for(*vals))) + "\n"


def check(con, cfg, query):
    """Free / mine / teammate for a pasted link, @handle or email. Used by the panel's "Check a link"."""
    q = (query or "").strip()
    if not q:
        return {"status": "empty"}
    keys = keys_for(q) | keys_for("@" + q.lstrip("@")) if "/" not in q and "@" not in q[1:] else keys_for(q)
    mine = keys & set(my_claims(con).split())
    if mine:
        return {"status": "mine", "detail": "You already have this person or post (queued, contacted or applied)."}
    t = Taken(cfg)
    who = None
    for k in keys:
        who = t.owner_of.get(k)
        if who:
            break
    if who:
        return {"status": "teammate", "who": who, "detail": f"{who} already has this person or post. Leave it to them."}
    known = owner(members(cfg), q) if members(cfg) else None
    return {"status": "free", "detail": "Nobody on the team has contacted this one yet.",
            "split_owner": known if q.startswith("UC") else None}


# ---------------------------------------------------------------- git plumbing (never touches the checkout)

def _auth():
    """Use the GitHub CLI's sign-in for this call only (scheduled runs can't answer a login prompt),
    without changing anyone's global git config. Falls back to git's own helpers when gh isn't installed."""
    gh = shutil.which("gh")
    if not gh:
        return []
    gh = gh.replace("\\", "/")
    return ["-c", "credential.https://github.com.helper=", "-c", f"credential.https://github.com.helper=!'{gh}' auth git-credential"]


def _git(*args, stdin=None, env=None):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", **(env or {}))
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: no console pop-ups under pythonw
    try:  # bytes in/out: text mode would turn \n into \r\n on Windows and change the blobs per platform
        p = subprocess.run(["git", *_auth(), "-C", str(ROOT), *args], input=stdin.encode() if stdin is not None else None,
                           capture_output=True, timeout=90, env=e, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 1, "", str(ex)
    dec = lambda b: b.decode("utf-8", "replace").strip()
    return p.returncode, dec(p.stdout), dec(p.stderr)


def _git_ready():
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        return False
    rc, out, _ = _git("remote", "get-url", "origin")
    return rc == 0 and bool(out)


def _repo(cfg):
    r = (cfg.get("team") or {}).get("repo")
    if r:
        return r
    if _git_ready():
        url = _git("remote", "get-url", "origin")[1]
        m = re.search(r"github\.com[/:]([^/]+/[^/.]+)", url)
        if m:
            return m.group(1)
    return REPO_DEFAULT


def _fetch():
    """True if the remote team branch exists (and is now at REMOTE_REF)."""
    rc, _, err = _git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:{REMOTE_REF}")
    if rc == 0:
        return True
    if "couldn't find remote ref" in err.lower():
        _git("update-ref", "-d", REMOTE_REF)
        return False
    raise RuntimeError(f"git fetch failed: {err[:300]}")


def _read_git():
    rc, out, _ = _git("ls-tree", "-r", "--name-only", REMOTE_REF)
    files = out.splitlines() if rc == 0 else []
    return {f: _git("show", f"{REMOTE_REF}:{f}")[1] for f in files}


def _read_https(repo):
    """Read-only copy of the team branch through the GitHub API (works on a public repo without git or sign-in)."""
    def get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "outreach-team-sync", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8"))
    base = f"https://api.github.com/repos/{repo}"
    try:
        tree = get(f"{base}/git/trees/{BRANCH}?recursive=1")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    out = {}
    for t in tree.get("tree", []):
        if t.get("type") == "blob" and re.fullmatch(r"members\.json|(claimed|installs)/[a-z0-9_-]+\.txt", t["path"]):
            out[t["path"]] = base64.b64decode(get(f"{base}/git/blobs/{t['sha']}")["content"]).decode("utf-8")
    return out


def _parse(files):
    mem = json.loads(files.get("members.json") or "[]")
    claims, installs = {}, {}
    for f, text in files.items():
        m = re.fullmatch(r"(claimed|installs)/([a-z0-9_-]+)\.txt", f)
        if m:
            (claims if m.group(1) == "claimed" else installs)[m.group(2)] = text.split()
    return mem, claims, {k: (v[0] if v else "") for k, v in installs.items()}


def _commit_push(changes, who, message):
    """Write {path: text} on top of the remote team branch and push. Retries when a teammate pushed first."""
    for _ in range(4):
        has_base = _fetch()
        idx = WORK / f".team-index-{os.getpid()}"
        env = {"GIT_INDEX_FILE": str(idx), "GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": f"{who}@outreach.team",
               "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": f"{who}@outreach.team"}
        try:
            rc, _, err = _git("read-tree", *([REMOTE_REF] if has_base else ["--empty"]), env=env)
            if rc:
                raise RuntimeError(f"read-tree: {err[:200]}")
            for path, text in changes(has_base).items():
                rc, blob, err = _git("hash-object", "-w", "--stdin", stdin=text)
                if rc:
                    raise RuntimeError(f"hash-object: {err[:200]}")
                _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = _git("write-tree", env=env)[1]
        finally:
            idx.unlink(missing_ok=True)
        if has_base and tree == _git("rev-parse", f"{REMOTE_REF}^{{tree}}")[1]:
            return "unchanged"
        rc, commit, err = _git("commit-tree", tree, *(["-p", REMOTE_REF] if has_base else []), "-m", message, env=env)
        if rc:
            raise RuntimeError(f"commit-tree: {err[:200]}")
        rc, _, err = _git("push", "--quiet", "origin", f"{commit}:refs/heads/{BRANCH}")
        if rc == 0:
            _git("update-ref", REMOTE_REF, commit)
            return "pushed"
        if "rejected" not in err and "fetch first" not in err and "non-fast-forward" not in err:
            raise RuntimeError(f"git push failed: {err[:300]}")
    raise RuntimeError("git push kept losing the race to a teammate; try again")


# ---------------------------------------------------------------- commands

def install_id(cfg):
    t = cfg.setdefault("team", {})
    if not t.get("install"):
        t["install"] = uuid.uuid4().hex[:12]
        save_config(cfg)
    return t["install"]


def _write_status(st):
    STATUS_FILE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return st


def sync(con, publish=True, take_over=False, quiet=False):
    """Fetch the member list and teammates' claims; publish this PC's claims. Returns a status dict."""
    say = (lambda *a: None) if quiet else print
    cfg = load_config()
    name = me(cfg)
    st = {"synced": now(), "me": name, "mode": "git" if _git_ready() else "https", "conflict": None, "error": None}
    if (cfg.get("team") or {}).get("solo"):
        st.update(mode="solo", members=[name])
        say("team: solo mode (team.solo is true), nothing to sync")
        return _write_status(st)
    try:
        files = (_read_git() if _fetch() else None) if st["mode"] == "git" else _read_https(_repo(cfg))
    except Exception as e:
        old = status()
        st.update(error=f"{type(e).__name__}: {str(e)[:200]}", members=members(cfg),
                  conflict=old.get("conflict") if old.get("me") == name else None)
        say(f"team: sync failed ({st['error']}), using the last synced copy")
        return _write_status(st)
    has_branch = files is not None
    mem, claims, installs = _parse(files or {})
    if not mem:
        mem = list((cfg.get("team") or {}).get("members") or [])
    mine_id = install_id(cfg)
    if installs.get(name) and installs[name] != mine_id and not take_over:
        st["conflict"] = (f"Another PC already uses the team name '{name}'. Join under your own name "
                          f"(`python engine.py team-join yourname`). Same person on a new PC? "
                          f"Run `python engine.py team-sync --take-over`.")
    t = cfg.setdefault("team", {})
    if mem and t.get("members") != mem:
        t["members"] = mem
        save_config(cfg)
    CLAIMS_CACHE.write_text(json.dumps(claims), encoding="utf-8")
    if name not in mem:
        say(f"team: WARNING '{name}' is not in the member list {mem}. Run `python engine.py team-join {name}`; "
            "until then this PC drafts nothing.")
    published, text = None, ""
    if publish and name in mem and not st["conflict"]:
        text = my_claims(con)
        if st["mode"] != "git":
            published = "read-only (no git checkout: install with git clone so teammates see your claims)"
        elif claims.get(name) != text.split() or installs.get(name) != mine_id or not has_branch:
            def changes(has_base):
                out = {f"claimed/{name}.txt": text, f"installs/{name}.txt": mine_id + "\n"}
                if not has_base:
                    out["members.json"] = json.dumps(mem, indent=1) + "\n"
                return out
            published = _commit_push(changes, name, f"claims: {name} {now()[:10]}")
        else:
            published = "unchanged"
    others = sum(len(v) for k, v in claims.items() if k != name)
    st.update(members=mem, claims_mine=len(text.split()) if text else len(claims.get(name, [])),
              claims_others=others, published=published)
    if st["conflict"]:
        say("team: CONFLICT " + st["conflict"])
    say(f"team: {len(mem)} members {mem}, this PC is '{name}', {others} claims from teammates"
        + (f", mine: {published}" if published else ""))
    return _write_status(st)


def join(con, name):
    name = name.strip().lower()
    if not NAME_RE.match(name):
        sys.exit("team name: 2-24 characters, lowercase letters, digits, - or _ (e.g. 'sami')")
    if not _git_ready():
        sys.exit("team-join needs the git checkout from GitHub (git clone), not a ZIP download.")
    cfg = load_config()
    mine_id = install_id(cfg)
    holder = {}

    def changes(has_base):
        files = _read_git() if has_base else {}
        mem, _, installs = _parse(files)
        if not mem:  # no list on GitHub yet: start from the one this PC already had, if any
            mem = list((cfg.get("team") or {}).get("members") or [])
        if installs.get(name) and installs[name] != mine_id:
            raise SystemExit(f"'{name}' is already used by another PC. Pick a different team name.")
        if name not in mem:
            mem = mem + [name]  # append only: earlier members keep everything they own
        holder["mem"] = mem
        return {"members.json": json.dumps(mem, indent=1) + "\n", f"installs/{name}.txt": mine_id + "\n"}

    state = _commit_push(changes, name, f"join: {name}")
    cfg = load_config()
    cfg.setdefault("team", {})["me"] = name
    cfg["team"]["members"] = holder["mem"]
    cfg["team"].pop("solo", None)
    save_config(cfg)
    print(f"team: '{name}' is in ({state}). Members in order: {holder['mem']}")
    sync(con)
