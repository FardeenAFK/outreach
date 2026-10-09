"""Team split: every lead and job post belongs to exactly one teammate, the same answer on every PC.

The member list and each member's claimed leads live on the repo's `team` branch (members.json and
claimed/<name>.txt), apart from the code on main. `sync()` fetches that branch, keeps the member list in
config.json -> team.members and the claims in work/team-claims.json, then publishes this PC's own claims.
Offline or without git, the last synced copy is used. Claims are hashes, so the branch holds no names or links.

Rules for members.json: names are only ever APPENDED. Removing or reordering a name reshuffles who owns what.
Someone who stops using the system keeps their place in the list.

    python engine.py team-sync          # fetch members + claims, publish mine (daily and finish run it too)
    python engine.py team-join NAME     # add NAME to the team and make this PC that person
"""
import hashlib
import json
import os
import re
import subprocess
import sys

from core import ROOT, WORK, load_config, save_config, today

BRANCH = "team"
REMOTE_REF = f"refs/remotes/origin/{BRANCH}"
CLAIMS_CACHE = WORK / "team-claims.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,23}$")


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
    if len(members) < 2:
        return members[0]
    h = hashlib.sha1(lead_id.encode()).hexdigest()
    j = _jump(int(hashlib.sha1(b"jump:" + lead_id.encode()).hexdigest()[:16], 16), len(members))
    return members[j] if j >= 2 else members[int(h, 16) % 2]


def me(cfg):
    return (cfg.get("team") or {}).get("me", "me")


def members(cfg):
    """Synced member list; a PC with no team at all works solo and owns every lead."""
    return (cfg.get("team") or {}).get("members") or [me(cfg)]


def claim_key(lead_id):
    return hashlib.sha1(b"claim:" + lead_id.encode()).hexdigest()[:16]


class Taken:
    """`lead_id in taken` = a teammate already queued or contacted it (before it was assigned here)."""

    def __init__(self, cfg):
        mine = me(cfg)
        try:
            claims = json.loads(CLAIMS_CACHE.read_text(encoding="utf-8")) if CLAIMS_CACHE.exists() else {}
        except (OSError, ValueError):
            claims = {}
        self.keys = {k for name, ks in claims.items() if name != mine for k in ks}
        legacy = ROOT / "claimed_elsewhere.json"  # the old zip package shipped raw ids
        self.ids = set(json.loads(legacy.read_text(encoding="utf-8"))) if legacy.exists() else set()

    def __contains__(self, lead_id):
        return lead_id in self.ids or claim_key(lead_id) in self.keys


def my_claims(con):
    """Every lead and job this PC has queued, contacted or applied to, as sorted hashes."""
    ids = {r[0] for r in con.execute(
        "SELECT id FROM leads WHERE status IN ('to_draft','queued','contacted','replied','won','dead') "
        "UNION SELECT DISTINCT lead_id FROM messages WHERE lead_id IS NOT NULL "
        "UNION SELECT id FROM jobs WHERE status IN ('to_draft','drafted','applied')")}
    return "\n".join(sorted(claim_key(i) for i in ids)) + "\n"


# ---------------------------------------------------------------- git plumbing (never touches the checkout)

def _git(*args, stdin=None, env=None):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", **(env or {}))
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: no console pop-ups under pythonw
    try:  # bytes in/out: text mode would turn \n into \r\n on Windows and change the blobs per platform
        p = subprocess.run(["git", "-C", str(ROOT), *args], input=stdin.encode() if stdin is not None else None,
                           capture_output=True, timeout=90, env=e, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 1, "", str(ex)
    dec = lambda b: b.decode("utf-8", "replace").strip()
    return p.returncode, dec(p.stdout), dec(p.stderr)


def _ready():
    if not (ROOT / ".git").exists():
        return "this folder is not a git checkout"
    rc, out, _ = _git("remote", "get-url", "origin")
    return None if rc == 0 and out else "no 'origin' remote"


def _fetch():
    """True if the remote team branch exists (and is now at REMOTE_REF)."""
    rc, _, err = _git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:{REMOTE_REF}")
    if rc == 0:
        return True
    if "couldn't find remote ref" in err.lower():
        _git("update-ref", "-d", REMOTE_REF)
        return False
    raise RuntimeError(f"git fetch failed: {err[:300]}")


def _read_remote():
    rc, out, _ = _git("ls-tree", "-r", "--name-only", REMOTE_REF)
    files = out.splitlines() if rc == 0 else []
    mem = []
    if "members.json" in files:
        mem = json.loads(_git("show", f"{REMOTE_REF}:members.json")[1] or "[]")
    claims = {}
    for f in files:
        m = re.fullmatch(r"claimed/([a-z0-9_-]+)\.txt", f)
        if m:
            claims[m.group(1)] = _git("show", f"{REMOTE_REF}:{f}")[1].split()
    return mem, claims


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

def _store(cfg, mem, claims):
    t = cfg.setdefault("team", {})
    if mem and t.get("members") != mem:
        t["members"] = mem
        save_config(cfg)
    CLAIMS_CACHE.write_text(json.dumps(claims, indent=0), encoding="utf-8")


def sync(con, publish=True):
    """Fetch the member list and teammates' claims; publish this PC's claims. Never fatal for the daily run."""
    cfg = load_config()
    why = _ready()
    if why:
        print(f"team: {why}, using the last synced copy ({len(members(cfg))} members)")
        return
    name = me(cfg)
    has_branch = _fetch()
    mem, claims = _read_remote() if has_branch else ([], {})
    if not mem:  # first PC to sync creates the list from its config
        mem = members(cfg)
    if name not in mem:
        print(f"team: WARNING '{name}' is not in the member list {mem}. Run `python engine.py team-join {name}`; "
              "until then this PC drafts nothing.")
    _store(cfg, mem, claims)
    if publish and name in mem:
        text = my_claims(con)
        if claims.get(name) != text.split() or not has_branch:
            def changes(has_base):
                out = {f"claimed/{name}.txt": text}
                if not has_base:
                    out["members.json"] = json.dumps(mem, indent=1) + "\n"
                return out
            state = _commit_push(changes, name, f"claims: {name} {today()}")
            print(f"team: published {len(text.split())} claims ({state})")
    others = sum(len(v) for k, v in claims.items() if k != name)
    print(f"team: {len(mem)} members {mem}, this PC is '{name}', {others} leads claimed by teammates")


def join(con, name):
    name = name.strip().lower()
    if not NAME_RE.match(name):
        sys.exit("team name: 2-24 characters, lowercase letters, digits, - or _ (e.g. 'sami')")
    why = _ready()
    if why:
        sys.exit(f"team-join needs the git checkout from GitHub ({why}).")
    cfg = load_config()
    holder = {}

    def changes(has_base):
        mem = _read_remote()[0] if has_base else []
        if not mem:  # no list on GitHub yet: start from the one this PC already had, if any
            mem = list((cfg.get("team") or {}).get("members") or [])
        if name not in mem:
            mem = mem + [name]  # append only: earlier members keep everything they own
        holder["mem"] = mem
        return {"members.json": json.dumps(mem, indent=1) + "\n"}

    state = _commit_push(changes, name, f"join: {name}")
    cfg.setdefault("team", {})["me"] = name
    cfg["team"]["members"] = holder["mem"]
    save_config(cfg)
    print(f"team: '{name}' is in ({state}). Members in order: {holder['mem']}")
    sync(con)
