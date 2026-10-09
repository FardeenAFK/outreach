// Outreach Queue side panel. All data comes from the local Outreach server.
// It never reads or scripts LinkedIn / Instagram / X pages: it only opens a URL and copies text.

const DEFAULT_SERVER = "http://127.0.0.1:8777";
const LIMITS = { x: 450, instagram: 450, linkedin: 600, facebook: 600 };
const NAMES = { x: "X", instagram: "Instagram", linkedin: "LinkedIn", facebook: "Facebook" };
const STEP_LABEL = (m) =>
  m.platform === "linkedin"
    ? ["Connect (no note)", "First DM", "Follow-up 1", "Follow-up 2"][m.step] || `Follow-up ${m.step - 1}`
    : m.step === 0 ? "First message" : `Follow-up ${m.step}`;

let state = { server: DEFAULT_SERVER, account: "me", tab: "today" };

// ---------- tiny DOM helper (textContent only: lead data is untrusted web text)
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (id) => document.getElementById(id);
const fmt = (n) => (n == null ? "?" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n));
const safeUrl = (u) => (typeof u === "string" && /^https:\/\//.test(u) ? u : null);

async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  let r;
  try { r = await fetch(state.server + path, opts); }
  catch (e) { setDot(false); throw new Error("server not reachable"); }
  setDot(true);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function setDot(ok) { const d = $("dot"); if (d) d.classList.toggle("ok", ok); }

// ---------- toast with Undo (every destructive click can be reversed for 10 s)
let toastTimer;
function toast(text, undo, kind = "") {
  let t = $("toast");
  if (!t) { t = h("div", { id: "toast" }); document.body.append(t); }
  t.className = `show ${kind}`;
  t.replaceChildren(...[h("span", {}, text), undo && h("button", { onclick: async () => {
    t.className = ""; clearTimeout(toastTimer);
    try { await undo(); } catch (e) { toast("Undo failed: " + e.message, null, "bad"); return; }
    RENDER[state.tab]();
  } }, "Undo")].filter(Boolean));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ""; }, undo ? 10000 : 6000);
}

function serverDown(section, err) {
  section.replaceChildren(
    h("div", { class: "err" },
      h("b", {}, "Outreach server not reachable. "),
      "Double-click ", h("code", {}, "Start Outreach.bat"), " in Documents\\Outreach, then reopen this tab.",
      h("div", { class: "muted" }, String(err && err.message || err)))
  );
}

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

// ---------- Today
async function renderToday() {
  const sec = $("today");
  let q;
  try { q = await api(`/api/queue?account=${encodeURIComponent(state.account)}`); }
  catch (e) { return serverDown(sec, e); }
  const nodes = [];
  let anything = false;
  const left = Object.values(q).reduce((a, g) => a + (g.cap ? g.items.length : 0), 0);
  if (left) nodes.push(h("div", { class: "summary" },
    h("b", {}, `${left} to send today`), ` · about ${Math.max(1, Math.round(left * 0.3))} min · `,
    h("span", { class: "muted" }, "Open DM + copy → paste → Enter → Sent ✓ → next")));
  for (const [plat, g] of Object.entries(q)) {
    if (!g.cap) continue;
    anything = true;
    nodes.push(h("div", { class: "plat", "data-platform": plat, "data-sent": g.sent, "data-cap": g.cap },
      h("span", { class: "name" }, NAMES[plat] || plat),
      h("span", { class: "muted" }, `${g.sent}/${g.cap} sent today`)));
    nodes.push(h("div", { class: "bar" }, h("div", { style: `width:${Math.min(100, (100 * g.sent) / g.cap)}%` })));
    if (!g.remaining) { nodes.push(h("div", { class: "done" }, "✓ Daily cap reached, stop here for today.")); continue; }
    if (!g.items.length) { nodes.push(h("div", { class: "empty" }, "Nothing queued. The next daily run adds more.")); continue; }
    for (const m of g.items) nodes.push(messageCard(m));
  }
  if (!anything) nodes.push(h("div", { class: "empty" }, "No platforms enabled for this account (Settings → caps)."));
  sec.replaceChildren(...nodes);
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

function messageCard(m) {
  const ta = h("textarea", {});
  ta.value = m.body || "";
  const limit = LIMITS[m.platform];
  const count = h("div", { class: "count" });
  const upd = () => { count.textContent = `${ta.value.length}/${limit}`; count.classList.toggle("over", ta.value.length > limit); };
  ta.addEventListener("input", () => { upd(); ta.dataset.dirty = "1"; }); upd();
  const isConnect = m.platform === "linkedin" && m.step === 0;
  const direct = !isConnect && dmUrl(m) !== m.profile_url;
  const open = () => openAndCopy(isConnect ? m.profile_url : dmUrl(m), isConnect ? "" : ta.value, ta);
  const undo = (what) => () => api("/api/mark", { kind: "message", id: m.id, action: "undo", undo: what });
  // one click at a time: a double click must never count twice
  const guarded = (fn) => async (ev) => {
    const b = ev.currentTarget;
    if (b.disabled) return;
    b.disabled = true;
    try { await fn(); } catch (e) { toast("Not saved: " + e.message, null, "bad"); b.disabled = false; }
  };
  const card = h("div", { class: "card", "data-platform": m.platform },
    h("div", { class: "top" },
      h("a", { class: "title", href: safeUrl(m.channel) || "#", target: "_blank" }, m.name || m.lead_id),
      h("span", { class: `tag ${m.platform}` }, STEP_LABEL(m))),
    h("div", { class: "muted" }, `${fmt(m.subs)} subs · score ${m.score ?? "?"}`,
      m.latest ? [" · latest: ", h("a", { href: safeUrl(m.latest_url) || "#", target: "_blank" }, m.latest)] : null),
    m.hiring ? h("div", { class: "hiring" }, "HIRING: ",
      h("a", { href: safeUrl(m.hiring.url) || "#", target: "_blank" }, m.hiring.title), ` · ${m.hiring.pay}`) : null,
    m.signals && m.signals.length ? h("ul", { class: "sig" }, m.signals.slice(0, 4).map((s) => h("li", {}, s))) : null,
    isConnect ? h("div", { class: "warn" }, "Open the profile and press Connect with NO note. Then Sent → next.") : [ta, count],
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: open },
        isConnect ? "Open profile" : direct ? "Open DM + copy" : "Open profile + copy"),
      h("button", { class: "ok", title: "Marks this one sent, then opens + copies the next one", onclick: guarded(async () => {
        const r = await api("/api/mark", { kind: "message", id: m.id, action: "sent", body: isConnect ? null : ta.value });
        card.remove();
        if (!r.already) bumpCounter(m.platform);
        toast(`Sent to ${m.name || "lead"}.`, undo("sent"));
        const next = $("today").querySelector(`.card[data-platform="${m.platform}"]`);
        if (next && next.openCard) await next.openCard();  // one click: done + next DM ready to paste
      }) }, "Sent ✓ → next"),
      direct ? h("button", { class: "btn", onclick: () => openAndCopy(m.profile_url, ta.value, ta) }, "Profile") : null,
      h("button", { class: "btn", onclick: guarded(async () => {
        await api("/api/mark", { kind: "message", id: m.id, action: "later" }); card.remove();
        toast(`${m.name || "Lead"} moved to tomorrow.`, undo("later"));
      }) }, "Tomorrow"),
      h("button", { class: "btn", onclick: guarded(async () => {
        await api("/api/mark", { kind: "message", id: m.id, action: "skipped" }); card.remove();
        toast(`Skipped ${m.name || "lead"}.`, undo("skipped"));
      }) }, "Skip"))
  );
  card.openCard = open;
  return card;
}

// Update "n/cap sent today" in place, so the list isn't re-rendered while you're working through it.
function bumpCounter(platform) {
  const head = $("today").querySelector(`.plat[data-platform="${platform}"]`);
  if (!head) return;
  const sent = +head.dataset.sent + 1, cap = +head.dataset.cap;
  head.dataset.sent = sent;
  head.querySelector(".muted").textContent = `${sent}/${cap} sent today`;
  const bar = head.nextElementSibling && head.nextElementSibling.firstChild;
  if (bar) bar.style.width = `${Math.min(100, (100 * sent) / cap)}%`;
  if (sent >= cap) {
    $("today").querySelectorAll(`.card[data-platform="${platform}"]`).forEach((c) => c.remove());
    head.nextElementSibling.after(h("div", { class: "done" }, "✓ Daily cap reached, stop here for today."));
  }
}

// ---------- Replies / pipeline
async function renderPipeline() {
  const sec = $("pipeline");
  let rows;
  try { rows = await api(`/api/pipeline?account=${encodeURIComponent(state.account)}`); }
  catch (e) { return serverDown(sec, e); }
  if (!rows.length) return sec.replaceChildren(h("div", { class: "empty" }, "No one contacted yet."));
  const mk = (r, action, label, cls = "btn") => h("button", { class: cls, onclick: async (ev) => {
    ev.currentTarget.disabled = true;
    try {
      await api("/api/mark", { kind: "lead", id: r.id, action, account: state.account });
    } catch (e) { toast("Not saved: " + e.message, null, "bad"); return renderPipeline(); }
    const undo = action === "accepted" ? null :
      () => api("/api/mark", { kind: "lead", id: r.id, action: "undo", undo: action, account: state.account });
    toast(`${r.name}: ${label.replace(/[^\w ]/g, "").trim().toLowerCase()}.`, undo);
    renderPipeline();
  } }, label);
  sec.replaceChildren(
    h("p", { class: "muted" }, "Check your inboxes yourself, then mark who answered. Marking a reply stops all follow-ups to that person."),
    ...rows.map((r) => h("div", { class: "card" },
      h("div", { class: "top" },
        h("a", { class: "title", href: safeUrl(r.url) || "#", target: "_blank" }, r.name),
        h("span", { class: `tag ${r.platform}` }, r.status === "replied" ? "replied" : `${r.touches} sent`)),
      h("div", { class: "muted" }, `last message ${String(r.last_sent || "").slice(0, 10)}`),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => openAndCopy(r.profile_url, "") }, "Open"),
        r.awaiting_accept ? mk(r, "accepted", "Accepted ✓ (queue DM)", "primary") : null,
        r.status !== "replied" ? mk(r, "replied", "Replied", "ok") : null,
        mk(r, "won", "Won 🎉"),
        mk(r, "dead", "Dead")))));
}

// ---------- Jobs
// Boards whose terms forbid bots/scraping: checked by hand, one click each.
// Ranked by the 2026-09-28 live research (JOB-SITES.md). "Remote" is usually country-locked:
// these links open searches that are either location-free or filtered to who can apply.
const BOARD_GROUPS = [
  ["Best for you", [
    ["YT Jobs: editing jobs (0% fee, worldwide)", "https://ytjobs.co/job/search/video_editor"],
    ["OnlineJobs.ph: video editor (270+, very active)", "https://www.onlinejobs.ph/jobseekers/jobsearch?jobkeyword=video+editor"],
    ["Himalayas: video editor (shows who can apply)", "https://himalayas.app/jobs/video-editor"],
    ["Jobgether: video editor (country eligibility)", "https://jobgether.com/remote-jobs/video-editor"],
    ["Remote Rocketship: open to Bangladesh", "https://www.remoterocketship.com/country/bangladesh/jobs/video-editor"],
  ]],
  ["Agencies hiring editors (steady work)", [
    ["Increditors: YouTube + Reels editor roles", "https://increditors.com/careers/"],
    ["Vidpros: video editor (email CV + portfolio)", "https://vidpros.com/hiring/video-editor"],
  ]],
  ["Marketplaces (manual only)", [
    ["Upwork: YouTube editing, newest", "https://www.upwork.com/nx/search/jobs/?q=youtube%20video%20editor&sort=recency"],
    ["LinkedIn Jobs: video editor, remote, 24h", "https://www.linkedin.com/jobs/search/?keywords=video%20editor&f_WT=2&f_TPR=r86400"],
    ["Freelancer.com: video editing", "https://www.freelancer.com/jobs/video-editing"],
    ["Workana: video editor (few bids)", "https://www.workana.com/en/jobs?query=video+editor"],
    ["Contra: video editor", "https://contra.com/jobs?q=video%20editor"],
    ["yt.careers: YouTube jobs (starter pay)", "https://yt.careers/"],
  ]],
];

async function renderJobs() {
  const sec = $("jobs");
  const boards = h("div", { class: "boards" },
    ...BOARD_GROUPS.map(([title, links]) => h("details", { open: title === "Best for you" },
      h("summary", {}, title),
      ...links.map(([label, url]) => h("a", { class: "board", href: url, target: "_blank" }, label)))));
  let rows;
  try { rows = await api("/api/jobs"); } catch (e) { return serverDown(sec, e); }
  if (!rows.length) return sec.replaceChildren(h("div", { class: "empty" }, "No drafted proposals right now."), boards);
  sec.replaceChildren(boards, h("h3", {}, "Drafted proposals"),
    h("p", { class: "muted" }, "Proposals are drafted for you. Open the job, paste, adjust and submit it yourself."),
    ...rows.map((j) => {
      const ta = h("textarea", {}); ta.value = j.proposal || "";
      const card = h("div", { class: "card" },
        h("div", { class: "top" }, h("span", { class: "title" }, j.title), h("span", { class: "tag" }, `fit ${Math.round(j.fit)}`)),
        h("div", { class: "muted" }, `${j.company || ""} · ${j.pay} · posted ${j.posted}`),
        ta,
        h("div", { class: "actions" },
          h("button", { class: "primary", onclick: () => openAndCopy(j.url, ta.value, ta) }, "Open job + copy"),
          ...[["applied", "Applied ✓", "ok"], ["skipped", "Skip", "btn"]].map(([action, label, cls]) =>
            h("button", { class: cls, onclick: async (ev) => {
              ev.currentTarget.disabled = true;
              try { await api("/api/mark", { kind: "job", id: j.id, action, account: state.account }); card.remove(); }
              catch (e) { ev.currentTarget.disabled = false; toast("Not saved: " + e.message, null, "bad"); }
            } }, label))));
      return card;
    }));
}

// ---------- Stats
async function renderStats() {
  const sec = $("stats");
  let s;
  try { s = await api(`/api/stats?account=${encodeURIComponent(state.account)}`); } catch (e) { return serverDown(sec, e); }
  const sentToday = Object.values(s.sent_today).reduce((a, b) => a + b, 0);
  const rate = s.total.sent ? ((100 * s.total.replied) / s.total.sent).toFixed(1) + "%" : "n/a";
  const kpi = (v, l) => h("div", { class: "kpi" }, h("b", {}, v), h("span", {}, l));
  sec.replaceChildren(
    h("div", { class: "kpis" },
      kpi(sentToday, "messages sent today"), kpi(s.applied_today, "jobs applied today"),
      kpi(s.week.sent, "sent, last 7 days"), kpi(s.week.replied, "replies, last 7 days"),
      kpi(rate, "all-time reply rate"), kpi(s.total.won, "clients won")),
    h("h3", {}, "Today by platform"),
    ...Object.keys(s.sent_today).map((p) => h("div", {}, `${NAMES[p] || p}: ${s.sent_today[p]} / ${s.caps[p]} (queued ${s.queued_due[p]})`)),
    h("h3", {}, "Pipeline"),
    h("div", { class: "muted" }, "Leads: " + Object.entries(s.leads).map(([k, v]) => `${k} ${v}`).join(" · ")),
    h("div", { class: "muted" }, "Jobs: " + Object.entries(s.jobs).map(([k, v]) => `${k} ${v}`).join(" · ")));
}

// ---------- Settings
async function renderSettings() {
  $("server").value = state.server;
  let c;
  try { c = await api("/api/config"); } catch (e) { $("saved").textContent = "Server not reachable."; return; }
  const acc = c.accounts[state.account] || {};
  for (const p of ["x", "instagram", "linkedin", "facebook"]) $("cap-" + p).value = (acc.caps || {})[p] ?? 0;
  const f = c.filters;
  for (const k of ["min_subs", "max_subs", "max_days_since_upload", "min_uploads_90d"]) $("f-" + k).value = f[k];
  $("f-niches").value = (f.niches || []).join(", ");
  $("f-exclude_keywords").value = (f.exclude_keywords || []).join(", ");
  $("f-countries_allow").value = (f.countries_allow || []).join(", ");
  $("f-faceless_only").checked = !!f.faceless_only;
  $("f-require_dm_contact").checked = !!f.require_dm_contact;
}

async function saveSettings() {
  state.server = $("server").value.trim().replace(/\/$/, "") || DEFAULT_SERVER;
  await chrome.storage.local.set({ server: state.server });
  const list = (id) => $(id).value.split(",").map((s) => s.trim()).filter(Boolean);
  const num = (id) => { const v = parseInt($(id).value, 10); return Number.isFinite(v) ? v : undefined; }; // blank = unchanged
  try {
    await api("/api/config", {
      account: state.account,
      caps: { x: num("cap-x"), instagram: num("cap-instagram"), linkedin: num("cap-linkedin"), facebook: num("cap-facebook") },
      filters: {
        min_subs: num("f-min_subs"), max_subs: num("f-max_subs"),
        max_days_since_upload: num("f-max_days_since_upload"), min_uploads_90d: num("f-min_uploads_90d"),
        niches: list("f-niches"), exclude_keywords: list("f-exclude_keywords"), countries_allow: list("f-countries_allow"),
        faceless_only: $("f-faceless_only").checked, require_dm_contact: $("f-require_dm_contact").checked,
      },
    });
    $("saved").textContent = "Saved ✓";
    renderSettings(); // show the clamped caps the server actually stored
  } catch (e) { $("saved").textContent = "Save failed: " + e.message; }
}

// ---------- shell
const RENDER = { today: renderToday, pipeline: renderPipeline, jobs: renderJobs, stats: renderStats, settings: renderSettings };
let timer;
function refreshSoon() { clearTimeout(timer); timer = setTimeout(() => RENDER[state.tab](), 150); }

function show(tab) {
  state.tab = tab;
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll("main section").forEach((s) => (s.hidden = s.id !== tab));
  RENDER[tab]();
}

async function init() {
  try { Object.assign(state, await chrome.storage.local.get(["server", "account"])); } catch (_) {}
  state.server = state.server || DEFAULT_SERVER;
  state.account = state.account || "me";
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  $("save").addEventListener("click", saveSettings);
  $("account").addEventListener("change", async (e) => {
    state.account = e.target.value;
    await chrome.storage.local.set({ account: state.account });
    show(state.tab);
  });
  try {
    await api("/api/health");
    $("dot").classList.add("ok");
    const c = await api("/api/config");
    $("account").replaceChildren(...Object.entries(c.accounts).map(([id, a]) => h("option", { value: id }, a.label || id)));
    if (!c.accounts[state.account]) state.account = Object.keys(c.accounts)[0];
    $("account").value = state.account;
  } catch (_) { /* each tab shows its own "server down" hint */ }
  show("today");
}

// Refresh when the panel comes back into view, but never wipe a message you are editing.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  const editing = document.activeElement && document.activeElement.tagName === "TEXTAREA";
  if (editing || document.querySelector("textarea[data-dirty]")) return;
  refreshSoon();
});
init();
