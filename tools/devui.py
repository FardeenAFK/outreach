"""Dev harness: serves the side panel as a normal web page (with a chrome.* stub) plus the API,
against COPIES of outreach.db and config.json, so UI testing never touches real data.
    python tools/devui.py [port]   -> http://127.0.0.1:8798/
"""
import os, shutil, sqlite3, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="outreach-dev-"))
src, dst = sqlite3.connect(ROOT / "outreach.db"), sqlite3.connect(tmp / "outreach.db")
src.backup(dst); src.close(); dst.close()
shutil.copy(ROOT / "config.json", tmp / "config.json")
os.environ["OUTREACH_DB"] = str(tmp / "outreach.db")
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
core.CONFIG_PATH = tmp / "config.json"
core.DB_PATH = tmp / "outreach.db"
import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8798
STUB = """<script>
(function () {
  // Real Chrome has a non-replaceable window.chrome: add properties instead of replacing it.
  const c = window.chrome || (window.chrome = {});
  const def = (k, v) => { try { Object.defineProperty(c, k, { value: v, configurable: true }); } catch (e) { c[k] = v; } };
  const demo = /[?&]demo/.test(location.search);
  window.__copied = "";
  const show = (url) => {
    console.log("open", url);
    if (!demo) return;
    let b = document.getElementById("demo");
    if (!b) {
      b = document.createElement("div"); b.id = "demo";
      b.style.cssText = "position:sticky;top:0;z-index:99;background:#12351f;color:#bff5d3;border:2px solid #3ecf8e;" +
        "border-radius:8px;margin:6px 0;padding:10px 12px;font:13px/1.45 Segoe UI";
      document.querySelector("main").prepend(b);
    }
    b.textContent = "";
    const h = document.createElement("b"); h.textContent = "DEMO \\u2192 opens: " + url;
    const p = document.createElement("div"); p.style.marginTop = "4px";
    p.textContent = 'Copied: "' + window.__copied.slice(0, 110) + '\\u2026"  \\u2192 you paste + press Enter';
    b.append(h, p);
  };
  def("storage", { local: { get: async () => ({ server: location.origin }), set: async () => {} } });
  def("tabs", { query: async () => [], update: (id, o) => show(o.url), create: (o) => show(o.url) });
  if (demo) {  // never touch the real clipboard during a demo
    try { Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true }); } catch (e) {}
  }
  // ?demo=1&auto=N plays N steps by itself (1 = Open DM + copy, 2 = Sent -> next, 3 = Undo): used to record the demo
  const auto = +(new URLSearchParams(location.search).get("auto") || 0);
  if (demo && auto) window.addEventListener("load", async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    await sleep(1500);
    const ig = document.querySelector('.plat[data-platform="instagram"]');
    document.querySelectorAll("#today > *").forEach((el) => { if (ig.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING && !el.classList.contains("summary")) el.remove(); });
    const card = () => document.querySelector('.card[data-platform="instagram"]');
    if (auto >= 1) { card().querySelector(".primary").click(); await sleep(700); }
    if (auto >= 2) { card().querySelector(".ok").click(); await sleep(1500); }
    if (auto >= 3) { document.querySelector("#toast button").click(); await sleep(1500); }
    window.scrollTo(0, 0);
    document.title = "DONE";
  });
})();
</script>"""
EXT = ROOT / "extension"
TYPES = {".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".html": "text/html"}


class Dev(server.Handler):
    def _origin_ok(self):
        return True  # same-origin dev page, copied data only

    def do_GET(self):
        if self.path.startswith("/api/"):
            return super().do_GET()
        path = self.path.split("?")[0]
        name = "sidepanel.html" if path in ("/", "/index.html") else path.lstrip("/")
        f = (EXT / name).resolve()
        if EXT not in f.parents or not f.is_file():
            return self._send(404, {"error": "not found"})
        body = f.read_bytes()
        if name == "sidepanel.html":
            body = body.replace(b"<script src=", STUB.encode() + b"<script src=", 1)
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


print(f"dev UI on http://127.0.0.1:{PORT}/  (data copy in {tmp})", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Dev).serve_forever()
