"""Validate drafted DMs before import.
    python tools/check_drafts.py work/leads_to_draft.json work/drafts_batch1.json [more drafts...]
Checks: char limits, links where not allowed, follow-up count, banned cliches, invented experience,
and near-duplicate wording across ALL messages (X/IG flag identical DMs)."""
import json, re, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402

BANNED = ["love your content", "huge fan", "hope this finds you", "came across your channel", "quick question",
          "just bumping", "i hope you're doing well", "big fan"]
CLAIMS = [r"\bmy portfolio\b", r"\byears of experience\b", r"\bi've worked with\b", r"\bmy clients\b",
          r"\bi have worked (with|for)\b", r"\bprevious clients\b", r"\b\d+\+? years\b", r"\bmy reel\b"]
# the sender's own software is private: never pitch it (names come from config.json -> private_tools)
PRIVATE = [r"\bbridge i built\b", r"\b(tool|plugin|panel|software|app)s? i (built|made|wrote)\b",
           r"\bmy own (tool|plugin|panel|software)"]
URL = re.compile(r"https?://|www\.|\.com\b|\.co\b|\.io\b", re.I)


def sender_rules():
    """Per-person rules from config.json: editing software the sender does NOT have, and their private tool names."""
    try:
        cfg = core.load_config()
    except (OSError, ValueError):
        return [], []
    names = lambda key: [re.escape(t.lower().strip()) for t in cfg.get(key, []) if t.strip()]
    claims = [rf"\b(in|use|using|with|edit in|edit with|cut in) {t}\b" for t in names("not_installed")]
    claims += [rf"\b{t}( pro| resolve)? and after effects\b" for t in names("not_installed")]
    return claims, [rf"\b{t}\b" for t in names("private_tools")]


def shingles(t, n=6):
    w = re.findall(r"[a-z0-9']+", t.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def main(export, *drafts):
    meta = {l["id"]: l for l in json.load(open(export, encoding="utf-8"))["leads"]}
    problems, msgs, seen = [], [], set()
    extra_claims, extra_private = sender_rules()
    claims, private = CLAIMS + extra_claims, PRIVATE + extra_private
    for f in drafts:
        d = json.load(open(f, encoding="utf-8"))
        for l in d.get("leads", []):
            m = meta.get(l["id"])
            if not m:
                problems.append(f"{f}: unknown id {l['id']}"); continue
            seen.add(l["id"])
            name, first, fus = m["name"], l.get("first", ""), l.get("followups", [])
            if not first.strip():
                problems.append(f"{name}: empty first message")
            if len(first) > m["char_limit"]:
                problems.append(f"{name}: {len(first)} chars > {m['char_limit']}")
            if len(fus) != m["followups_needed"]:
                problems.append(f"{name}: {len(fus)} follow-ups, needs {m['followups_needed']}")
            for i, fu in enumerate(fus):
                if len(fu) > 200:
                    problems.append(f"{name}: follow-up {i + 1} is {len(fu)} chars > 200")
            for t in [first] + fus:
                low = t.lower()
                if not m["links_allowed"] and URL.search(t):
                    problems.append(f"{name}: link on {m['platform']}: {t[:60]}")
                problems += [f"{name}: cliche '{b}'" for b in BANNED if b in low]
                problems += [f"{name}: experience claim /{c}/" for c in claims if re.search(c, low)]
                problems += [f"{name}: mentions private self-built tool /{c}/" for c in private if re.search(c, low)]
                if re.search(r"\[(add|insert)|\{|\}|TODO", t, re.I):
                    problems.append(f"{name}: placeholder left in text")
                msgs.append((name, t))
        for sid in d.get("skip_leads", []):
            seen.add(sid)
    # near-duplicate wording across different leads
    owners = defaultdict(set)
    for name, t in msgs:
        for s in shingles(t):
            owners[s].add(name)
    dup = defaultdict(set)
    for s, names in owners.items():
        if len(names) > 4:  # the offer can recur a little; the same line in 5+ DMs looks like a template
            dup[s] = names
    for s, names in list(dup.items())[:15]:
        problems.append(f"shared phrase in {len(names)} leads: '{s}'")
    missing = [meta[i]["name"] for i in meta if i not in seen]
    print(f"{len(msgs)} messages checked, {len(seen)}/{len(meta)} leads covered")
    if missing:
        print("NOT COVERED:", ", ".join(missing))
    print("\n".join(problems) if problems else "ALL CHECKS PASSED")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
