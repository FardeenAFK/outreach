// Outreach queue: side panel + web page. All data comes from the local Outreach server.
// It never reads or scripts LinkedIn / Instagram / X / job-board pages: it only opens a URL and copies text.

const DEFAULT_SERVER = "http://127.0.0.1:8777";
const LIMITS = { x: 450, instagram: 450, linkedin: 600, facebook: 600, email: 1500 };
const NAMES = { x: "X", instagram: "Instagram", linkedin: "LinkedIn", facebook: "Facebook", email: "Email" };
const STEP_LABEL = (m) =>
  m.platform === "linkedin"
    ? ["Connect (no note)", "First DM", "Follow-up 1", "Follow-up 2"][m.step] || `Follow-up ${m.step - 1}`
    : m.step === 0 ? (m.platform === "email" ? "First email" : "First message") : `Follow-up ${m.step}`;

let state = { server: DEFAULT_SERVER, account: "me", tab: "today", filter: "all", focus: false, theme: "system",
              jobsView: "proposals", repliesFilter: "all", repliesQuery: "", config: null, team: null };

// ---------- tiny DOM helper (textContent only: lead data is untrusted web text)
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k === "value") el.value = v ?? "";
    else if (k === "checked") el.checked = !!v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (id) => document.getElementById(id);
// replaceChildren without the "null" text: optional parts are written as cond ? node : null
const put = (el, ...kids) => el.replaceChildren(...kids.flat(3).filter((k) => k !== null && k !== undefined && k !== false));
const fmt = (n) => (n == null ? "?" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n));
const safeUrl = (u) => (typeof u === "string" && /^(https:\/\/|mailto:)/i.test(u) ? u : null);
const enc = encodeURIComponent;
const ago = (iso) => {
  if (!iso) return "never";
  const d = Math.round((Date.now() - new Date(iso.replace(" ", "T")).getTime()) / 864e5);
  return d <= 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
};
const agoMin = (iso) => {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 6e4);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  let r;
  try { r = await fetch(state.server + path, opts); }
  catch (e) { setDot(false); throw new Error("server not reachable"); }
  setDot(true);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const err = new Error(data.error || r.statusText); err.status = r.status; err.data = data; throw err; }
  return data;
}
function setDot(ok) { const d = $("dot"); if (d) d.classList.toggle("up", ok); }
async function store(o) { Object.assign(state, o); try { await chrome.storage.local.set(o); } catch (_) {} }

// ---------- toasts with Undo (every destructive click can be reversed for 10 s; U does the latest)
let lastUndo = null;
function toast(text, undo, kind = "") {
  const box = $("toasts");
  const t = h("div", { class: `toast ${kind}`, role: "status" }, h("span", {}, text));
  const close = () => { t.remove(); if (lastUndo === run) lastUndo = null; };
  const run = undo ? async () => {
    close();
    try { await undo(); } catch (e) { toast("Undo failed: " + e.message, null, "bad"); return; }
    RENDER[state.tab]();
  } : null;
  if (run) { t.append(h("button", { onclick: run }, "Undo")); lastUndo = run; }
  box.append(t);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(close, undo ? 10000 : 5000);
}

function serverDown(section, err) {
  put(section, h("div", { class: "errbox" },
    h("b", {}, "The Outreach server isn't running. "),
    "Double-click ", h("code", {}, "Start Outreach.bat"), " in the Outreach folder, then reload this page.",
    h("div", { class: "muted small" }, String((err && err.message) || err))));
}
const skeleton = (n = 2) => Array.from({ length: n }, () => h("div", { class: "skeleton" }));
const empty = (title, text) => h("div", { class: "empty" }, h("b", {}, title), text);

// Copy FIRST; if the copy fails, do NOT open the page: pasting would send the previous
// person's message to this one. The text is selected instead so Ctrl+C works.
async function openAndCopy(url, text, textarea) {
  const safe = safeUrl(url);
  if (text) {
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const msg = "Copy failed. The message is selected: press Ctrl+C, then open it again.";
      if (textarea) {  // warn ON the card, so an Undo toast that is showing stays usable
        textarea.focus(); textarea.select();
        let w = textarea.parentElement.querySelector(".copywarn");
        if (!w) { w = h("div", { class: "copywarn" }); textarea.after(w); }
        w.textContent = msg;
      } else toast(msg, null, "bad");
      return false;
    }
  }
  if (!safe) return true;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) chrome.tabs.update(tab.id, { url: safe });
  else chrome.tabs.create({ url: safe });
  return true;
}

// Direct-to-DM links where the platform has one; X and LinkedIn open the profile.
function dmUrl(m) {
  try {
    const u = new URL(m.profile_url);
    const handle = u.pathname.split("/").filter(Boolean)[0];
    if (m.platform === "instagram" && handle) return `https://ig.me/m/${handle}`;
    // m.me only works for a plain username: /pages/..., /people/..., profile.php?id= must open the profile
    if (m.platform === "facebook" && handle && !["profile.php", "pages", "people", "pg", "groups"].includes(handle))
      return `https://m.me/${handle}`;
  } catch (_) {}
  return m.profile_url;
}

// Email: a compose window in the sender's own mailbox, with everything filled in. They press Send.
function composeUrl(to, subject, body) {
  const client = (state.config && state.config.email && state.config.email.client) || "gmail";
  if (client === "outlook") return `https://outlook.live.com/mail/0/deeplink/compose?to=${enc(to)}&subject=${enc(subject)}&body=${enc(body)}`;
  if (client === "mailto") return `mailto:${to}?subject=${enc(subject)}&body=${enc(body)}`;
  return `https://mail.google.com/mail/?view=cm&fs=1&to=${enc(to)}&su=${enc(subject)}&body=${enc(body)}`;
}

function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.max(84, ta.scrollHeight + 2) + "px"; }

async function removeCard(card) {
  card.classList.remove("current");
  if (reduced()) { card.remove(); return; }
  card.style.height = card.offsetHeight + "px";
  card.classList.add("leaving");
  await new Promise((r) => requestAnimationFrame(r));
  Object.assign(card.style, { height: "0px", marginBottom: "0px", paddingTop: "0px", paddingBottom: "0px", borderWidth: "0px",
                              transition: "height .22s var(--ease), margin .22s, padding .22s, opacity .18s, transform .2s" });
  await new Promise((r) => setTimeout(r, 230));
  card.remove();
}

// ---------- Today
const visibleCards = () => [...document.querySelectorAll("#today .card.msg:not(.leaving)")];
function setCurrent(card) {
  document.querySelectorAll("#today .card.current").forEach((c) => c.classList.remove("current"));
  const target = card || visibleCards()[0];
  if (target) target.classList.add("current");
  return target;
}
const currentCard = () => document.querySelector("#today .card.msg.current:not(.leaving)") || setCurrent();

async function renderToday() {
  const sec = $("today");
  if (!sec.childElementCount) put(sec, ...skeleton(3));
  let q;
  try { q = await api(`/api/queue?account=${enc(state.account)}`); } catch (e) { return serverDown(sec, e); }
  const groups = Object.entries(q).filter(([, g]) => g.cap);
  const left = groups.reduce((a, [, g]) => a + g.items.length, 0);
  const sent = groups.reduce((a, [, g]) => a + g.sent, 0);
  setBadge("today", left);
  const nodes = [];
  const t = state.team;
  if (t && !t.ready) nodes.push(h("div", { class: "errbox" }, h("b", {}, "New drafts are paused: "), t.why,
    " ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); show("team"); } }, "Open Team")));
  if (!groups.length) {
    nodes.push(empty("No platforms switched on", "Set a daily cap above 0 in Settings → Platforms."));
    return put(sec, ...nodes);
  }
  const chips = h("div", { class: "chips" },
    h("button", { class: "fchip" + (state.filter === "all" ? " on" : ""), onclick: () => setFilter("all") }, "All ", h("span", { "data-chip": "all" }, left)),
    ...groups.map(([p, g]) => h("button", { class: "fchip" + (state.filter === p ? " on" : ""), onclick: () => setFilter(p) },
      h("span", { class: `pdot ${p}` }), `${NAMES[p]} `, h("span", { "data-chip": p }, g.items.length))));
  nodes.push(h("div", { class: "hero" },
    h("div", { class: "row" },
      h("div", {}, h("div", { class: "big" }, left), h("div", { class: "sub" }, left === 1 ? "message to send today" : "messages to send today")),
      h("span", { class: "spacer" }),
      h("div", { class: "row" },
        h("span", { class: "hint" }, left ? `about ${Math.max(1, Math.round(left * 0.3))} min` : sent ? "all done for today" : ""),
        h("button", { class: "btn", title: "Show one person at a time (F)", onclick: toggleFocus }, state.focus ? "Show all" : "Focus mode"))),
    h("div", { class: "progress", title: `${sent} sent today` }, h("div", { style: `width:${sent + left ? (100 * sent) / (sent + left) : 0}%` })),
    chips,
    h("div", { class: "hint", style: "margin-top:10px" },
      "Open + copy → paste → Enter → ", h("b", {}, "Sent ✓ → next"), ". Keys: ", h("kbd", {}, "O"), " open, ", h("kbd", {}, "S"),
      " sent, ", h("kbd", {}, "J"), "/", h("kbd", {}, "K"), " move, ", h("kbd", {}, "?"), " all keys.")));
  for (const [plat, g] of groups) {
    if (state.filter !== "all" && state.filter !== plat) continue;
    nodes.push(h("div", { class: "plat", "data-platform": plat, "data-sent": g.sent, "data-cap": g.cap },
      h("span", { class: `pdot ${plat}` }), h("span", { class: "name" }, NAMES[plat] || plat),
      h("div", { class: "mini" }, h("div", { style: `width:${Math.min(100, (100 * g.sent) / g.cap)}%` })),
      h("span", { class: "muted small count-label" }, `${g.sent}/${g.cap} sent`)));
    if (!g.remaining) { nodes.push(h("div", { class: "done" }, "✓ Daily cap reached. Stop here for today.")); continue; }
    if (!g.items.length) { nodes.push(h("div", { class: "muted small", style: "padding:2px 4px 6px" }, "Nothing queued. The next morning run adds more.")); continue; }
    for (const m of g.items) nodes.push(messageCard(m));
  }
  if (!left) nodes.push(empty(sent ? "Done for today 🎉" : "Nothing to send yet",
    sent ? "Check the Replies tab, then come back tomorrow." : "The 7am run drafts new messages every morning."));
  put(sec, ...nodes);
  sec.classList.toggle("focus-mode", state.focus);
  sec.querySelectorAll("textarea.body").forEach(autosize);
  setCurrent();
}
function setFilter(p) { store({ filter: p }); renderToday(); }
function toggleFocus() { store({ focus: !state.focus }); renderToday(); }

function messageCard(m) {
  const isConnect = m.platform === "linkedin" && m.step === 0;
  const isEmail = m.platform === "email";
  const ta = h("textarea", { class: "body", value: m.body || "", "aria-label": "Message", spellcheck: "true" });
  const subj = isEmail ? h("input", { class: "subject", value: m.subject || "", "aria-label": "Subject", placeholder: "Subject" }) : null;
  const limit = LIMITS[m.platform];
  const savedDot = h("span", { class: "saved-dot" }, "saved ✓");
  const count = h("div", { class: "count" }, savedDot, h("span", {}));
  const upd = () => { count.lastChild.textContent = `${ta.value.length}/${limit}`; count.classList.toggle("over", ta.value.length > limit); };
  let dirty = false;
  ta.addEventListener("input", () => { upd(); autosize(ta); dirty = true; ta.dataset.dirty = "1"; });
  if (subj) subj.addEventListener("input", () => { dirty = true; });
  const saveEdit = async () => {  // keep edits even if the page reloads before you send
    if (!dirty) return;
    dirty = false;
    try {
      await api("/api/mark", { kind: "message", id: m.id, action: "edit", body: ta.value, subject: subj ? subj.value : undefined });
      delete ta.dataset.dirty;
      savedDot.classList.add("show"); setTimeout(() => savedDot.classList.remove("show"), 1500);
    } catch (_) { dirty = true; }
  };
  ta.addEventListener("blur", saveEdit);
  if (subj) subj.addEventListener("blur", saveEdit);
  upd();
  const fullText = () => (isEmail && m.footer ? `${ta.value.trim()}\n\n${m.footer}` : ta.value);
  const to = isEmail ? String(m.profile_url || "").replace(/^mailto:/i, "") : "";
  const target = () => (isConnect ? m.profile_url : isEmail ? composeUrl(to, subj.value, fullText()) : dmUrl(m));
  const direct = !isConnect && !isEmail && dmUrl(m) !== m.profile_url;
  const card = h("div", { class: "card msg", "data-platform": m.platform, "data-id": m.id });
  const open = async () => { setCurrent(card); return openAndCopy(target(), isConnect ? "" : fullText(), ta); };
  const undo = (what) => () => api("/api/mark", { kind: "message", id: m.id, action: "undo", undo: what });
  const guarded = (fn) => async (ev) => {  // one action per card: a double click or a held key must never count twice
    const b = ev && ev.currentTarget;
    if (card.busy || (b && b.disabled)) return;
    card.busy = true;
    if (b) b.disabled = true;
    try { await fn(); } catch (e) { toast("Not saved: " + e.message, null, "bad"); card.busy = false; if (b) b.disabled = false; }
  };
  const nextAfter = () => {
    const list = visibleCards();
    const i = list.indexOf(card);
    return list.slice(i + 1).find((c) => c.dataset.platform === m.platform) || list[i + 1] || list[i - 1];
  };
  const sent = guarded(async () => {
    const r = await api("/api/mark", { kind: "message", id: m.id, action: "sent", body: isConnect ? null : ta.value,
                                       subject: subj ? subj.value : undefined });
    const next = nextAfter();
    removeCard(card);
    if (!r.already) bumpCounter(m.platform);
    toast(`Sent to ${m.name || "lead"}.`, undo("sent"));
    if (next) { setCurrent(next); next.scrollIntoView({ block: "nearest", behavior: reduced() ? "auto" : "smooth" });
                if (next.dataset.platform === m.platform && next.actions) await next.actions.open(); }
  });
  const later = guarded(async () => {
    await api("/api/mark", { kind: "message", id: m.id, action: "later" });
    const next = nextAfter(); removeCard(card); setCurrent(next);
    toast(`${m.name || "Lead"} moved to tomorrow.`, undo("later")); bumpLeft(m.platform);
  });
  const skip = guarded(async () => {
    await api("/api/mark", { kind: "message", id: m.id, action: "skipped" });
    const next = nextAfter(); removeCard(card); setCurrent(next);
    toast(`Skipped ${m.name || "lead"}.`, undo("skipped")); bumpLeft(m.platform);
  });
  card.actions = { open, sent, later, skip, edit: () => ta.focus() };
  card.addEventListener("pointerdown", () => setCurrent(card));
  const country = m.country ? ` · ${m.country}` : "";
  card.append(...[
    h("div", { class: "top" },
      h("div", {}, h("a", { class: "title", href: safeUrl(m.channel) || "#", target: "_blank", rel: "noopener" }, m.name || m.lead_id),
        h("div", { class: "meta" }, `${fmt(m.subs)} subs · score ${m.score ?? "?"}${country}`,
          m.latest ? [" · latest: ", h("a", { href: safeUrl(m.latest_url) || "#", target: "_blank", rel: "noopener" }, m.latest)] : null)),
      h("span", { class: `tag ${m.platform}` }, STEP_LABEL(m))),
    m.hiring ? h("div", { class: "hiring" }, "Hiring: ",
      h("a", { href: safeUrl(m.hiring.url) || "#", target: "_blank", rel: "noopener" }, m.hiring.title), m.hiring.pay ? ` · ${m.hiring.pay}` : "") : null,
    m.signals && m.signals.length ? h("details", { class: "why" }, h("summary", {}, "Why this lead"),
      h("ul", {}, m.signals.slice(0, 6).map((s) => h("li", {}, s)))) : null,
    isConnect ? h("div", { class: "warnbox" }, "Open the profile and press Connect with NO note. Then Sent → next.")
      : [subj, ta, isEmail && m.footer ? h("div", { class: "footer-preview", title: "Added to every email (Settings → Email)" }, m.footer) : null, count],
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: open, title: "O" },
        isConnect ? "Open profile" : isEmail ? "Open email + copy" : direct ? "Open DM + copy" : "Open profile + copy", h("kbd", {}, "O")),
      h("button", { class: "ok", title: "Marks this one sent, then opens + copies the next one (S)", onclick: sent }, "Sent ✓ → next", h("kbd", {}, "S")),
      direct ? h("button", { class: "btn ghost", onclick: () => openAndCopy(m.profile_url, ta.value, ta) }, "Profile") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", onclick: later, title: "T" }, "Tomorrow"),
      h("button", { class: "btn ghost", onclick: skip, title: "X" }, "Skip"))].flat(2).filter((k) => k != null && k !== false));
  return card;
}

// Update counters in place, so the list isn't re-rendered while you're working through it.
function bumpCounter(platform) {
  const head = $("today").querySelector(`.plat[data-platform="${platform}"]`);
  bumpLeft(platform);
  if (!head) return;
  const sent = +head.dataset.sent + 1, cap = +head.dataset.cap;
  head.dataset.sent = sent;
  head.querySelector(".count-label").textContent = `${sent}/${cap} sent`;
  head.querySelector(".mini > div").style.width = `${Math.min(100, (100 * sent) / cap)}%`;
  if (sent >= cap) {
    $("today").querySelectorAll(`.card[data-platform="${platform}"]`).forEach((c) => c.remove());
    head.after(h("div", { class: "done" }, "✓ Daily cap reached. Stop here for today."));
  }
}
function bumpLeft(platform) {
  const b = $("b-today"); const n = Math.max(0, (+b.textContent || 0) - 1);
  setBadge("today", n);
  const big = $("today").querySelector(".hero .big"); if (big) big.textContent = n;
  for (const k of ["all", platform]) {
    const c = $("today").querySelector(`[data-chip="${k}"]`); if (c) c.textContent = Math.max(0, +c.textContent - 1);
  }
}
function setBadge(tab, n) { const b = $("b-" + tab); if (b) b.textContent = n ? String(n) : ""; }

// ---------- Replies / pipeline
async function renderPipeline() {
  const sec = $("pipeline");
  if (!sec.childElementCount) put(sec, ...skeleton(2));
  let rows;
  try { rows = await api(`/api/pipeline?account=${enc(state.account)}`); } catch (e) { return serverDown(sec, e); }
  const replied = rows.filter((r) => r.status === "replied").length;
  setBadge("pipeline", replied);
  const F = { all: () => true, replied: (r) => r.status === "replied", waiting: (r) => r.status === "contacted",
              accept: (r) => r.awaiting_accept, won: (r) => r.status === "won" };
  const qx = state.repliesQuery.toLowerCase();
  const list = rows.filter(F[state.repliesFilter] || F.all).filter((r) => !qx || (r.name || "").toLowerCase().includes(qx));
  const mk = (r, action, label, cls = "btn") => h("button", { class: cls, onclick: async (ev) => {
    ev.currentTarget.disabled = true;
    try { await api("/api/mark", { kind: "lead", id: r.id, action, account: state.account }); }
    catch (e) { toast("Not saved: " + e.message, null, "bad"); return renderPipeline(); }
    const undo = action === "accepted" ? null :
      () => api("/api/mark", { kind: "lead", id: r.id, action: "undo", undo: action, account: state.account });
    toast(`${r.name}: ${label.replace(/[^\w ]/g, "").trim().toLowerCase()}.`, undo);
    renderPipeline();
  } }, label);
  const search = h("input", { type: "search", placeholder: "Search names…", value: state.repliesQuery, style: "max-width:220px",
    oninput: (e) => { state.repliesQuery = e.target.value; clearTimeout(search._t); search._t = setTimeout(renderPipeline, 200); } });
  const chip = (k, label) => h("button", { class: "fchip" + (state.repliesFilter === k ? " on" : ""),
    onclick: () => { state.repliesFilter = k; renderPipeline(); } }, label);
  put(sec, 
    h("p", { class: "muted", style: "margin-top:0" }, "Check your inboxes yourself, then mark who answered. Marking a reply stops all follow-ups to that person."),
    h("div", { class: "row", style: "margin-bottom:10px" },
      h("div", { class: "chips" }, chip("all", `All ${rows.length}`), chip("replied", `Replied ${replied}`),
        chip("waiting", "Waiting"), chip("accept", "LinkedIn: accept?"), chip("won", "Won")),
      h("span", { class: "spacer" }), search),
    list.length ? h("div", { class: "list" }, ...list.map((r) => h("div", { class: "item", "data-platform": r.platform },
      h("div", { class: "top" },
        h("a", { class: "title", href: safeUrl(r.url) || "#", target: "_blank", rel: "noopener" }, r.name),
        h("span", { class: `tag ${r.status === "replied" ? "good" : r.status === "won" ? "good" : r.platform}` },
          r.status === "replied" ? "replied" : r.status === "won" ? "won 🎉" : `${NAMES[r.platform] || r.platform} · ${r.touches} sent`)),
      h("div", { class: "muted small" }, `last message ${ago(r.last_sent)}`,
        r.next_followup ? ` · next follow-up ${r.next_followup}` : ""),
      h("div", { class: "actions", style: "margin-top:4px" },
        r.platform !== "email" ? h("button", { class: "btn", onclick: () => openAndCopy(r.profile_url, "") }, "Open") : null,
        r.awaiting_accept ? mk(r, "accepted", "Accepted ✓ (queue DM)", "primary") : null,
        r.status === "contacted" ? mk(r, "replied", "Replied", "ok") : null,
        r.status !== "won" ? mk(r, "won", "Won 🎉") : null,
        r.status !== "won" ? mk(r, "dead", "Dead", "btn ghost") : null)))) :
      empty(rows.length ? "Nobody matches" : "No one contacted yet", rows.length ? "Try another filter." : "People you mark Sent show up here."));
}

// ---------- Jobs: proposals, find jobs, tracker
function boardGroups(kw) {
  const q = enc(kw);
  return [
    ["People hiring right now (posts)", "Read the post, then DM or apply yourself. Check the link first so you don't double up with a teammate.", [
      ["X: “hiring” + " + kw + ", newest", `https://x.com/search?q=${enc(`hiring "${kw}"`)}&f=live`, "fresh posts, reply or DM by hand"],
      ["LinkedIn posts: hiring " + kw + ", past week", `https://www.linkedin.com/search/results/content/?keywords=${enc(`hiring ${kw}`)}&datePosted=%22past-week%22&sortBy=%22date_posted%22`, "comment or DM by hand"],
      ["Reddit r/HireAnEditor, newest", "https://www.reddit.com/r/HireAnEditor/new/", "creators post jobs daily"],
      ["Reddit r/forhire: [Hiring] " + kw, `https://www.reddit.com/r/forhire/search/?q=${enc(`hiring ${kw}`)}&restrict_sr=1&sort=new`, "filter by [Hiring]"],
      ["Reddit r/VideoEditingRequests", "https://www.reddit.com/r/VideoEditingRequests/new/", "small paid gigs"],
      ["Facebook groups: " + kw + " jobs", `https://www.facebook.com/search/groups/?q=${enc(kw + " jobs")}`, "join 3-5 active groups"],
    ]],
    ["Job boards (search by hand: these sites ban bots)", "Apply yourself, then press Log so the tracker and your teammates know.", [
      ["Indeed: " + kw + ", remote, last 3 days", `https://www.indeed.com/jobs?q=${q}&l=Remote&fromage=3&sort=date`, "check who can apply"],
      ["LinkedIn Jobs: " + kw + ", remote, last 24h", `https://www.linkedin.com/jobs/search/?keywords=${q}&f_WT=2&f_TPR=r86400`, "Easy Apply is fine, by hand"],
      ["Upwork: " + kw + ", newest", `https://www.upwork.com/nx/search/jobs/?q=${q}&sort=recency`, "manual only, no extensions"],
      ["Freelancer.com: video editing", "https://www.freelancer.com/jobs/video-editing/", "bidding war, manual only"],
      ["OnlineJobs.ph: " + kw, `https://www.onlinejobs.ph/jobseekers/jobsearch?jobkeyword=${q}`, "very active"],
      ["Himalayas: " + kw, `https://himalayas.app/jobs?q=${q}`, "shows eligible countries"],
      ["Jobgether: video editor", "https://jobgether.com/remote-jobs/video-editor", "shows eligible regions"],
      ["Remote Rocketship: open to Bangladesh", "https://www.remoterocketship.com/country/bangladesh/jobs/video-editor", "country-filtered"],
      ["PeoplePerHour: " + kw, `https://www.peopleperhour.com/freelance-jobs?q=${q}`, ""],
      ["Workana: " + kw, `https://www.workana.com/en/jobs?query=${q}`, "few bids per job"],
      ["Contra: " + kw, `https://contra.com/jobs?q=${q}`, "0% commission"],
      ["YT Jobs: editing jobs", "https://ytjobs.co/job/search/video_editor", "already scanned every morning"],
      ["yt.careers", "https://yt.careers/", "starter pay"],
    ]],
    ["Let clients come to you", "Listings that work while you sleep.", [
      ["Fiverr: see what sells for " + kw, `https://www.fiverr.com/search/gigs?query=${q}`, "copy the packaging, not the price"],
      ["Contra: make a free profile", "https://contra.com/", ""],
    ]],
    ["Agencies hiring editors (steady work)", "", [
      ["Increditors: YouTube + Reels editor roles", "https://increditors.com/careers/", ""],
      ["Vidpros: video editor (email CV + portfolio)", "https://vidpros.com/hiring/video-editor", ""],
    ]],
  ];
}

async function renderJobs() {
  const sec = $("jobs");
  const seg = h("div", { class: "seg", role: "tablist" },
    ...[["proposals", "Proposals"], ["find", "Find jobs"], ["tracker", "Tracker"]].map(([k, label]) =>
      h("button", { class: state.jobsView === k ? "on" : "", onclick: () => { store({ jobsView: k }); renderJobs(); } },
        label, h("span", { class: "badge", id: `jb-${k}` }))));
  const body = h("div", {}, ...skeleton(2));
  put(sec, seg, body);
  let jobs = [], apps = [];
  try { [jobs, apps] = await Promise.all([api("/api/jobs"), api("/api/applications")]); } catch (e) { return serverDown(body, e); }
  const due = apps.filter((a) => a.followup_now).length;
  $("jb-proposals").textContent = jobs.length || "";
  $("jb-tracker").textContent = due || "";
  setBadge("jobs", jobs.length + due);
  if (state.jobsView === "find") return renderFind(body);
  if (state.jobsView === "tracker") return renderTracker(body, apps);
  if (!jobs.length) return put(body, empty("No drafted proposals right now",
    "The morning run drafts proposals for new YT Jobs posts. Meanwhile: Find jobs."));
  put(body, 
    h("p", { class: "muted", style: "margin-top:0" }, "Drafted for you. Open the job, paste, adjust and submit it yourself. Applied ✓ adds it to the Tracker."),
    ...jobs.map((j) => {
      const ta = h("textarea", { class: "body", value: j.proposal || "" });
      const card = h("div", { class: "card" },
        h("div", { class: "top" }, h("div", {}, h("div", { class: "title" }, j.title),
          h("div", { class: "meta" }, [j.company, j.board, j.pay, j.posted && `posted ${j.posted}`].filter(Boolean).join(" · "))),
          h("span", { class: `tag ${j.fit >= 70 ? "good" : ""}` }, `fit ${Math.round(j.fit)}`)),
        ta,
        h("div", { class: "actions" },
          h("button", { class: "primary", onclick: () => openAndCopy(j.url, ta.value, ta) }, "Open job + copy"),
          ...[["applied", "Applied ✓", "ok"], ["skipped", "Skip", "btn ghost"]].map(([action, label, cls]) =>
            h("button", { class: cls, onclick: async (ev) => {
              ev.currentTarget.disabled = true;
              try { await api("/api/mark", { kind: "job", id: j.id, action, account: state.account }); }
              catch (e) { ev.currentTarget.disabled = false; return toast("Not saved: " + e.message, null, "bad"); }
              removeCard(card);
              toast(action === "applied" ? `Applied: ${j.title}. Added to the Tracker.` : `Skipped ${j.title}.`,
                () => api("/api/mark", { kind: "job", id: j.id, action: "undo", undo: action, account: state.account }));
            } }, label))));
      requestAnimationFrame(() => autosize(ta));
      return card;
    }));
}

function renderFind(body) {
  const kw = (state.config && state.config.jobs && state.config.jobs.search_keyword) || "video editor";
  const input = h("input", { value: kw, placeholder: "video editor", style: "max-width:240px", "aria-label": "Search keyword" });
  const apply = async () => {
    const v = input.value.trim(); if (!v) return;
    try { await api("/api/config", { jobs: { search_keyword: v } }); state.config.jobs.search_keyword = v; } catch (_) {}
    renderFind(body);
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
  put(body, 
    h("div", { class: "row", style: "margin-bottom:10px" }, h("span", { class: "muted" }, "Search for"), input,
      h("button", { class: "btn", onclick: apply }, "Update links")),
    h("p", { class: "muted small", style: "margin-top:0" },
      "YT Jobs and the boards with public feeds are scanned automatically every morning. The sites below forbid bots, so you open them yourself: one click each."),
    h("div", { class: "boards" }, ...boardGroups(kw).map(([title, note, links], i) => h("details", { open: i < 2 },
      h("summary", {}, title, h("span", { class: "muted small", style: "font-weight:400;margin-left:auto" }, `${links.length}`)),
      note ? h("div", { class: "muted small", style: "padding:0 12px 8px" }, note) : null,
      ...links.map(([label, url, why]) => h("div", { class: "board" },
        h("a", { href: url, target: "_blank", rel: "noopener" }, label, why ? h("span", { class: "why" }, why) : null),
        h("button", { class: "btn ghost small", title: "Log something you applied to or pitched from here",
          onclick: () => { store({ jobsView: "tracker" }); renderJobs().then(() => { const f = $("app-board"); if (f) { f.value = label.split(":")[0]; $("app-url").focus(); } }); } },
          "Log")))))));
}

function renderTracker(body, apps) {
  const url = h("input", { id: "app-url", placeholder: "Job or post link (https://…)" });
  const title = h("input", { placeholder: "Title (e.g. YouTube editor, 2 videos/week)" });
  const company = h("input", { placeholder: "Company / channel" });
  const board = h("input", { id: "app-board", placeholder: "Site (auto from link)" });
  const notes = h("input", { placeholder: "Notes (rate, contact, deadline…)" });
  const msg = h("div", {});
  const add = async (force = false) => {
    put(msg, );
    try {
      await api("/api/applications", { action: "add", url: url.value, title: title.value, company: company.value,
                                       board: board.value, notes: notes.value, force, account: state.account });
    } catch (e) {
      if (e.status === 409) {
        return put(msg, h("div", { class: `result ${e.data.status}` }, e.data.detail, " ",
          h("button", { class: "btn small", onclick: () => add(true) }, "Log it anyway")));
      }
      return toast("Not saved: " + e.message, null, "bad");
    }
    toast("Logged. Follow-up reminder in 7 days.");
    renderJobs();
  };
  const row = (a) => {
    const sel = h("select", { class: "compact", onchange: async () => {
      try { await api("/api/applications", { action: "update", id: a.id, status: sel.value }); toast(`Marked ${sel.value}.`); renderJobs(); }
      catch (e) { toast("Not saved: " + e.message, null, "bad"); }
    } }, ...["applied", "interview", "won", "lost", "ghosted"].map((s) => h("option", { value: s }, s)));
    sel.value = a.status;
    return h("div", { class: "item" },
      h("div", { class: "top" },
        a.url ? h("a", { class: "title", href: safeUrl(a.url) || "#", target: "_blank", rel: "noopener" }, a.title || a.url) : h("span", { class: "title" }, a.title),
        sel),
      h("div", { class: "muted small" }, [a.company, a.board, `logged ${ago(a.created)}`].filter(Boolean).join(" · "),
        a.followup_now ? h("span", { class: "tag warn", style: "margin-left:6px" }, "follow up now") : null),
      a.notes ? h("div", { class: "small" }, a.notes) : null,
      h("div", { class: "actions", style: "margin-top:2px" },
        a.followup_now ? h("button", { class: "btn small", onclick: async () => {
          await api("/api/applications", { action: "update", id: a.id, followed_up: true }); toast("Next reminder in 7 days."); renderJobs();
        } }, "Followed up ✓") : null,
        h("span", { class: "spacer" }),
        h("button", { class: "btn ghost small", onclick: async () => {
          if (!confirm("Delete this entry?")) return;
          await api("/api/applications", { action: "delete", id: a.id }); renderJobs();
        } }, "Delete")));
  };
  put(body, 
    h("div", { class: "panel" },
      h("h3", {}, "Log an application or a pitch ", h("small", {}, "(anything you did by hand: Indeed, Upwork, a DM from an X post…)")),
      h("div", { class: "grid2" }, h("label", {}, "Link", url), h("label", {}, "Title", title),
        h("label", {}, "Company / channel", company), h("label", {}, "Site", board)),
      h("label", {}, "Notes", notes),
      h("div", { class: "row", style: "margin-bottom:10px" }, h("button", { class: "primary", onclick: () => add(false) }, "Log it"),
        h("span", { class: "muted small" }, "Teammates see it too, so nobody applies twice.")),
      msg),
    apps.length ? h("div", { class: "list" }, ...apps.map(row)) : empty("Nothing logged yet", "Applied ✓ on a proposal or Log it above."));
}

// ---------- Team
async function loadTeam() {
  try { state.team = await api("/api/team"); } catch (_) { state.team = null; }
  const chip = $("teamchip"), t = state.team;
  if (!t) { chip.textContent = "Team ?"; chip.className = "chip-team"; return; }
  const st = t.status || {};
  chip.textContent = t.solo ? "Solo" : !t.ready ? "Team: paused" : `Team · ${t.members.length} · ${t.me}`;
  chip.className = "chip-team " + (t.ready ? (st.error ? "" : "is-ok") : "is-bad");
  chip.title = t.ready ? `This PC is '${t.me}'. Last sync ${agoMin(st.synced)}${st.error ? " (failed: " + st.error + ")" : ""}` : t.why;
  setBadge("team", t.ready ? 0 : "!");
}

async function renderTeam() {
  const sec = $("team");
  await loadTeam();
  const t = state.team;
  if (!t) return serverDown(sec, "no team info");
  const st = t.status || {};
  const result = h("div", {});
  const q = h("input", { placeholder: "Paste a profile / job link, @handle or email", "aria-label": "Link to check" });
  const check = async () => {
    if (!q.value.trim()) return;
    try {
      const r = await api(`/api/check?q=${enc(q.value.trim())}`);
      put(result, h("div", { class: `result ${r.status}` },
        r.status === "free" ? "✓ Free: " : r.status === "mine" ? "● Yours: " : r.status === "teammate" ? "✕ Taken: " : "", r.detail || ""));
    } catch (e) { toast(e.message, null, "bad"); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") check(); });
  const sync = h("button", { class: "btn", onclick: async (ev) => {
    ev.currentTarget.disabled = true; ev.currentTarget.textContent = "Syncing…";
    try { await api("/api/team/sync", {}); toast("Team synced."); } catch (e) { toast("Sync failed: " + e.message, null, "bad"); }
    renderTeam();
  } }, "Sync now");
  put(sec, 
    !t.ready ? h("div", { class: "errbox" }, h("b", {}, "New drafts are paused. "), t.why) : null,
    st.conflict && t.ready ? h("div", { class: "errbox" }, st.conflict) : null,
    h("div", { class: "panel" },
      h("h3", {}, "Your team"),
      t.solo ? h("p", {}, "Solo mode: this PC owns every lead. Turn it off in config.json (team.solo) to work with a team.") :
        h("div", { class: "members" }, ...(t.members.length ? t.members : ["(none yet)"]).map((m) =>
          h("span", { class: "member" + (m === t.me ? " me" : "") }, m === t.me ? `${m} (this PC)` : m))),
      h("div", { class: "muted small" },
        `Last sync ${agoMin(st.synced)}`, st.mode ? ` · ${st.mode === "git" ? "git (shares your claims)" : st.mode === "https" ? "read-only (ZIP install: your claims aren't shared)" : st.mode}` : "",
        st.claims_others != null ? ` · ${st.claims_others} people claimed by teammates · ${st.claims_mine || 0} by you` : "",
        st.error ? ` · last attempt failed: ${st.error}` : ""),
      h("div", { class: "row", style: "margin:10px 0" }, sync)),
    h("div", { class: "panel" },
      h("h3", {}, "Check a link before you message anyone by hand"),
      h("div", { class: "row" }, q, h("button", { class: "primary", onclick: check }, "Check")),
      result,
      h("p", { class: "muted small" }, "Works for X / Instagram / LinkedIn / Facebook profiles, YouTube channels, job posts and emails.")),
    h("div", { class: "panel" },
      h("h3", {}, "How the split works"),
      h("p", { class: "small" }, "Every client belongs to exactly one person, decided the same way on every PC. Everyone you queue, message or apply to is shared as a hash (no names or links), so a teammate's PC skips them. It syncs every 20 minutes while this page's server runs, plus at the start and end of each morning run."),
      h("p", { class: "small muted" }, "Adding a friend: invite their GitHub account to the repo, then on their PC Claude follows SETUP.md and runs ",
        h("code", {}, "python engine.py team-join theirname"), ". Never remove or reorder names.")));
}

// ---------- Stats
async function renderStats() {
  const sec = $("stats");
  if (!sec.childElementCount) put(sec, ...skeleton(2));
  let s;
  try { s = await api(`/api/stats?account=${enc(state.account)}`); } catch (e) { return serverDown(sec, e); }
  const sentToday = Object.values(s.sent_today).reduce((a, b) => a + b, 0);
  const rate = s.total.sent ? ((100 * s.total.replied) / s.total.sent).toFixed(1) + "%" : "n/a";
  const kpi = (v, l) => h("div", { class: "kpi" }, h("b", {}, v), h("span", {}, l));
  const max = Math.max(1, ...s.history.map((d) => d.sent + d.applied));
  const apps = s.applications || {};
  put(sec, 
    h("div", { class: "kpis" },
      kpi(sentToday, "sent today"), kpi(s.week.sent, "sent, last 7 days"), kpi(s.week.replied, "replies, last 7 days"),
      kpi(rate, "reply rate (all time)"), kpi((apps.applied || 0) + (apps.interview || 0), "open applications"),
      kpi(s.total.won, "clients won")),
    h("h3", {}, "Last 14 days"),
    h("div", { class: "chart", role: "img", "aria-label": "Messages sent and replies per day, last 14 days" },
      ...s.history.map((d) => h("div", { class: "col", title: `${d.day}: ${d.sent} sent, ${d.applied} applied, ${d.replied} replies` },
        h("div", { class: "r", style: `height:${(100 * d.replied) / max}%` }),
        h("div", { class: "s", style: `height:${(100 * (d.sent + d.applied)) / max}%` })))),
    h("div", { class: "chart-x" }, ...s.history.map((d, i) => h("span", {}, i % 2 ? "" : d.day.slice(8)))),
    h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:color-mix(in srgb, var(--accent) 70%, transparent)" }), "sent + applied"),
      h("span", {}, h("i", { style: "background:var(--ok)" }), "replies")),
    h("h3", {}, "By platform"),
    h("table", { class: "t" }, h("tr", {}, h("th", {}, "Platform"), h("th", {}, "Today"), h("th", {}, "Sent"), h("th", {}, "Replies"), h("th", {}, "Rate")),
      ...Object.entries(s.by_platform).filter(([p]) => (s.caps || {})[p] || s.by_platform[p].sent).map(([p, v]) => h("tr", {},
        h("td", {}, h("span", { class: `pdot ${p}`, style: "display:inline-block;margin-right:6px" }), NAMES[p] || p),
        h("td", {}, `${s.sent_today[p]} / ${(s.caps || {})[p] ?? "?"}`), h("td", {}, v.sent), h("td", {}, v.replied),
        h("td", {}, v.sent ? `${((100 * v.replied) / v.sent).toFixed(1)}%` : "–")))),
    h("h3", {}, "Pipeline"),
    h("div", { class: "muted small" }, "Leads: " + Object.entries(s.leads).map(([k, v]) => `${k} ${v}`).join(" · ")),
    h("div", { class: "muted small" }, "Jobs: " + Object.entries(s.jobs).map(([k, v]) => `${k} ${v}`).join(" · ")),
    Object.keys(apps).length ? h("div", { class: "muted small" }, "Tracker: " + Object.entries(apps).map(([k, v]) => `${k} ${v}`).join(" · ")) : null);
}

// ---------- Settings
async function renderSettings() {
  const sec = $("settings");
  let c;
  try { c = state.config = await api("/api/config"); } catch (e) { return serverDown(sec, e); }
  const acc = c.accounts[state.account] || Object.values(c.accounts)[0] || { caps: {} };
  const F = {};  // field registry: id -> element
  const inp = (id, label, value, attrs = {}) => h("label", {}, label, F[id] = h("input", { value, ...attrs }));
  const area = (id, label, value, rows = 3) => h("label", {}, label, F[id] = h("textarea", { rows, value }));
  const chk = (id, label, value) => h("label", { class: "check" }, F[id] = h("input", { type: "checkbox", checked: value }), label);
  const list = (v) => (v || []).join(", ");
  const p = c.profile || {}, f = c.filters || {}, e = c.email || {}, d = c.daily || {}, r = c.ramp || {}, j = c.jobs || {};
  const plats = c.platforms || Object.keys(NAMES);
  const theme = h("select", { onchange: (ev) => setTheme(ev.target.value) },
    ...[["system", "Match my system"], ["dark", "Dark"], ["light", "Light"]].map(([v, l]) => h("option", { value: v }, l)));
  theme.value = state.theme;
  const sources = Object.entries(j.sources || {});
  const status = h("span", { class: "muted small" });
  const notes = h("div", {});
  put(sec, 
    h("div", { class: "panel" }, h("h3", {}, "You ", h("small", {}, "(what every message is allowed to say)")),
      h("div", { class: "grid2" }, inp("sender_name", "Name at the end of messages", p.sender_name),
        h("label", {}, "Based in the US?", F.us_based = h("select", {}, h("option", { value: "no" }, "No"), h("option", { value: "yes" }, "Yes")))),
      inp("offer", "Your offer", p.offer),
      area("profile", "Skills, styles and software you actually have (messages never claim anything else)", p.profile, 5),
      h("div", { class: "grid2" }, inp("not_installed", "Software you DON'T have (comma separated)", list(p.not_installed), { placeholder: "Premiere, DaVinci" }),
        inp("private_tools", "Your own private tools (never mentioned)", list(p.private_tools)))),
    h("div", { class: "panel" }, h("h3", {}, "Platforms ", h("small", {}, "(daily caps; weeks 1-2 run at a lower ramp)")),
      h("div", { class: "grid3" }, ...plats.map((pl) => inp("cap-" + pl, `${NAMES[pl] || pl} (max ${c.max_caps[pl]})`, (acc.caps || {})[pl] ?? 0,
        { type: "number", min: 0, max: c.max_caps[pl] }))),
      h("p", { class: "muted small" }, "New Instagram account? Keep it at 5-10. Bans come from people ignoring or reporting you, not only from volume."),
      h("h3", {}, "Follow-up days ", h("small", {}, "(days after the first message, comma separated; empty = none)")),
      h("div", { class: "grid3" }, ...plats.map((pl) => inp("fu-" + pl, NAMES[pl] || pl, list(c.followup_days[pl]))))),
    h("div", { class: "panel" }, h("h3", {}, "Email ", h("small", {}, "(sent by you from your own mailbox, off until filled in)")),
      h("p", { class: "small muted", style: "margin-top:0" }, "Anti-spam law (CAN-SPAM, GDPR) needs a real postal address and a way to opt out in every cold email. Use a separate business address if you can, and keep it under 10 a day."),
      h("div", { class: "grid2" },
        h("label", {}, "Open emails in", F.client = h("select", {}, h("option", { value: "gmail" }, "Gmail"), h("option", { value: "outlook" }, "Outlook.com"), h("option", { value: "mailto" }, "My mail app"))),
        inp("address_line", "Postal address line (city + street or PO box)", e.address_line)),
      inp("optout_line", "Opt-out line", e.optout_line)),
    h("div", { class: "panel" }, h("h3", {}, "Daily volume"),
      h("div", { class: "grid3" },
        inp("new_leads_to_enrich", "Channels to check", d.new_leads_to_enrich, { type: "number", min: 1 }),
        inp("new_leads_to_draft", "Messages to draft", d.new_leads_to_draft, { type: "number", min: 1 }),
        inp("jobs_to_draft", "Job proposals", d.jobs_to_draft, { type: "number", min: 1 }),
        inp("week1_pct", "Week 1 ramp %", r.week1_pct, { type: "number", min: 10, max: 100 }),
        inp("week2_pct", "Week 2 ramp %", r.week2_pct, { type: "number", min: 10, max: 100 }))),
    h("div", { class: "panel" }, h("h3", {}, "Lead filters ", h("small", {}, "(used from the next morning run)")),
      h("div", { class: "grid2" },
        inp("min_subs", "Min subscribers", f.min_subs, { type: "number" }), inp("max_subs", "Max subscribers", f.max_subs, { type: "number" }),
        inp("max_days_since_upload", "Max days since last upload", f.max_days_since_upload, { type: "number" }),
        inp("min_uploads_90d", "Min uploads in 90 days", f.min_uploads_90d, { type: "number" })),
      area("niches", "Niches to search (comma separated)", list(f.niches), 2),
      area("exclude_keywords", "Skip channels mentioning", list(f.exclude_keywords), 2),
      area("countries_allow", "Only these countries (channels with no country listed are kept)", list(f.countries_allow), 2),
      chk("faceless_only", "Faceless channels only", f.faceless_only),
      chk("require_dm_contact", "Only channels with X / Instagram / LinkedIn listed", f.require_dm_contact)),
    h("div", { class: "panel" }, h("h3", {}, "Job search"),
      h("div", { class: "grid3" }, inp("search_keyword", "Keyword for the Find-jobs links", j.search_keyword || "video editor"),
        inp("country", "Your country (skip jobs not open to it)", j.country || "", { placeholder: "e.g. Bangladesh" }),
        inp("max_age_days", "Ignore posts older than (days)", j.max_age_days, { type: "number", min: 3, max: 90 })),
      area("jobs_include", "A job title must mention one of", list(j.include), 2),
      area("jobs_exclude", "Skip job titles with", list(j.exclude), 2),
      sources.length ? [h("div", { class: "muted small", style: "margin-bottom:6px" }, "Feeds scanned every morning:"),
        h("div", { class: "grid2" }, ...sources.map(([name, on]) => chk("src-" + name, name, on))),
        h("p", { class: "muted small" }, "Only sites that publish a feed for this are scanned. Sites that ban bots are one-click links under Jobs → Find jobs.")] : null),
    h("div", { class: "panel" }, h("h3", {}, "Appearance"), h("label", {}, "Theme", theme),
      chk("focus", "Start in focus mode (one person at a time)", state.focus),
      h("label", {}, "Local server", F.server = h("input", { value: state.server }))),
    notes,
    h("div", { class: "savebar" }, h("button", { class: "primary", onclick: save }, "Save settings"), status));
  F.us_based.value = p.us_based ? "yes" : "no";
  F.client.value = e.client || "gmail";
  sec.addEventListener("input", () => { status.textContent = "Unsaved changes"; }, { once: true });

  async function save() {
    const val = (id) => F[id].value.trim();
    const num = (id) => { const v = parseInt(F[id].value, 10); return Number.isFinite(v) ? v : undefined; };
    const arr = (id) => F[id].value.split(",").map((s) => s.trim()).filter(Boolean);
    const days = (id) => arr(id).map((x) => parseInt(x, 10)).filter(Number.isFinite);
    const body = {
      account: state.account,
      caps: Object.fromEntries(plats.map((pl) => [pl, num("cap-" + pl)])),
      followup_days: Object.fromEntries(plats.map((pl) => [pl, days("fu-" + pl)])),
      profile: { sender_name: val("sender_name"), offer: val("offer"), profile: val("profile"), us_based: F.us_based.value === "yes",
                 not_installed: arr("not_installed"), private_tools: arr("private_tools") },
      email: { client: F.client.value, address_line: val("address_line"), optout_line: val("optout_line") },
      daily: { new_leads_to_enrich: num("new_leads_to_enrich"), new_leads_to_draft: num("new_leads_to_draft"), jobs_to_draft: num("jobs_to_draft") },
      ramp: { week1_pct: num("week1_pct"), week2_pct: num("week2_pct") },
      filters: { min_subs: num("min_subs"), max_subs: num("max_subs"), max_days_since_upload: num("max_days_since_upload"),
                 min_uploads_90d: num("min_uploads_90d"), niches: arr("niches"), exclude_keywords: arr("exclude_keywords"),
                 countries_allow: arr("countries_allow"), faceless_only: F.faceless_only.checked, require_dm_contact: F.require_dm_contact.checked },
      jobs: { search_keyword: val("search_keyword"), country: val("country"), max_age_days: num("max_age_days"), include: arr("jobs_include"), exclude: arr("jobs_exclude"),
              sources: Object.fromEntries(sources.map(([name]) => [name, F["src-" + name].checked])) },
    };
    await store({ server: val("server").replace(/\/$/, "") || DEFAULT_SERVER, focus: F.focus.checked });
    try {
      const res = await api("/api/config", body);
      put(notes, ...(res.notes || []).map((n) => h("div", { class: "warnbox" }, n)));
      toast("Settings saved ✓");
      await renderSettings();
    } catch (err) { status.textContent = "Save failed: " + err.message; }
  }
}

// ---------- shell: tabs, keys, theme
const RENDER = { today: renderToday, pipeline: renderPipeline, jobs: renderJobs, team: renderTeam, stats: renderStats, settings: renderSettings };
let timer;
function refreshSoon() { clearTimeout(timer); timer = setTimeout(() => RENDER[state.tab](), 150); }

function show(tab) {
  store({ tab });
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll("main section").forEach((s) => (s.hidden = s.id !== tab));
  RENDER[tab]();
}

function setTheme(t) {
  store({ theme: t });
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

function showKeys() {
  if (document.querySelector(".overlay")) return;
  const rows = [["O / Enter", "Open + copy the current message"], ["S", "Sent ✓ → next"], ["J / K", "Next / previous person"],
    ["E", "Edit the message"], ["T", "Move to tomorrow"], ["X", "Skip this person"], ["U", "Undo the last action"],
    ["F", "Focus mode on/off"], ["1-6", "Switch tabs"], ["Esc", "Leave a text box / close this"]];
  const ov = h("div", { class: "overlay", onclick: (e) => { if (e.target === ov) ov.remove(); } },
    h("div", { class: "sheet", role: "dialog", "aria-label": "Keyboard shortcuts" },
      h("h2", {}, "Keyboard shortcuts"),
      h("div", { class: "keys" }, ...rows.flatMap(([k, d]) => [h("kbd", {}, k), h("span", {}, d)])),
      h("div", { class: "row", style: "margin-top:14px" }, h("span", { class: "spacer" }), h("button", { class: "btn", onclick: () => ov.remove() }, "Close"))));
  document.body.append(ov);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { const ov = document.querySelector(".overlay"); if (ov) return ov.remove(); }
  if (e.target.matches("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
  if (e.target.closest("button, a, summary") && (e.key === "Enter" || e.key === " ")) return;  // let the focused control act
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tabs = ["today", "pipeline", "jobs", "team", "stats", "settings"];
  if (/^[1-6]$/.test(e.key)) return show(tabs[+e.key - 1]);
  if (e.key === "?") return showKeys();
  if (e.key.toLowerCase() === "u" && lastUndo) return lastUndo();
  if (state.tab !== "today") return;
  const card = currentCard();
  const list = visibleCards();
  const k = e.key.toLowerCase();
  if (k === "f") return toggleFocus();
  if (!card) return;
  const move = (d) => { const n = list[Math.min(list.length - 1, Math.max(0, list.indexOf(card) + d))]; setCurrent(n);
                        n.scrollIntoView({ block: "nearest", behavior: reduced() ? "auto" : "smooth" }); };
  const map = { j: () => move(1), arrowdown: () => move(1), k: () => move(-1), arrowup: () => move(-1),
                o: card.actions.open, enter: card.actions.open, s: card.actions.sent, t: card.actions.later,
                x: card.actions.skip, e: card.actions.edit };
  if (map[k]) { e.preventDefault(); map[k](); }
});

async function init() {
  try { Object.assign(state, await chrome.storage.local.get(["server", "account", "tab", "filter", "focus", "theme", "jobsView"])); } catch (_) {}
  state.server = state.server || DEFAULT_SERVER;
  state.account = state.account || "me";
  setTheme(state.theme || "system");
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  $("theme").addEventListener("click", () => {
    const dark = state.theme === "dark" || (state.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    setTheme(dark ? "light" : "dark");
  });
  $("keys").addEventListener("click", showKeys);
  $("teamchip").addEventListener("click", () => show("team"));
  $("account").addEventListener("change", async (e) => { await store({ account: e.target.value }); show(state.tab); });
  try {
    await api("/api/health");
    state.config = await api("/api/config");
    const accs = Object.entries(state.config.accounts);
    $("account").replaceChildren(...accs.map(([id, a]) => h("option", { value: id }, a.label || id)));
    $("account").hidden = accs.length < 2;
    if (!state.config.accounts[state.account]) state.account = accs[0][0];
    $("account").value = state.account;
  } catch (_) { /* each tab shows its own "server down" hint */ }
  await loadTeam();
  show(RENDER[state.tab] ? state.tab : "today");
  // quiet background badges for the other tabs
  api("/api/jobs").then((j) => api("/api/applications").then((a) => setBadge("jobs", j.length + a.filter((x) => x.followup_now).length))).catch(() => {});
  api(`/api/pipeline?account=${enc(state.account)}`).then((r) => setBadge("pipeline", r.filter((x) => x.status === "replied").length)).catch(() => {});
}

// Refresh when the panel comes back into view, but never wipe a message you are editing.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  const editing = document.activeElement && document.activeElement.matches("textarea, input");
  if (editing || document.querySelector("textarea[data-dirty]")) return;
  if (state.tab !== "settings") refreshSoon();
  loadTeam();
});
init();
