"""
lectern web UI: a small JSON API + single-page app, Python standard library only.

    GET  /                      the app
    GET  /api/state             docs, schedule, options, sync status
    GET  /api/log               recent sync.log lines
    POST /api/sync              {"guid": optional}            sync everything or one doc
    POST /api/links/add         {"url", "folder"}
    POST /api/links/move        {"guid", "folder"}
    POST /api/links/remove      {"guid"}
    POST /api/scan              {"text"}                      find doc links in pasted text/HTML
    POST /api/scan/add          {"urls": [...], "folder"}
    GET  /api/search?q=         full-text search
    GET  /api/news              WHATS-NEW.md
    GET  /api/history           snapshots;  GET /api/history/<sha>  one snapshot's diff
    POST /api/schedule          {"mode", "times", "hours", "enabled"}
    POST /api/config            {"key", "value"}
    POST /api/pdf               {"guid"}
    GET  /api/folders           folder suggestions
    GET  /files/<path>          read-only files from the courses folder (the docs site lives here)
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lectern_sync as core
import lectern as cli

SYNC_LOCK = threading.Lock()
_proc = {"p": None, "what": None}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css",
        ".md": "text/plain; charset=utf-8", ".json": "application/json", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".pdf": "application/pdf", ".zip": "application/zip",
        ".mp4": "video/mp4", ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon",
        ".webmanifest": "application/manifest+json"}
STATIC_FILES = {"icon.svg", "favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
                "icon-maskable-512.png", "manifest.webmanifest"}


# ----------------------------------------------------------------- helpers
def sync_running():
    p = _proc["p"]
    if p and p.poll() is None:
        return _proc["what"] or "sync"
    try:  # a scheduled or CLI sync holds the lock file
        with open(core.LOCK_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return None
    except OSError:
        return "sync"


def start_sync(urls=None, what="Syncing all docs"):
    with SYNC_LOCK:
        if sync_running():
            return False
        cmd = [sys.executable, "-I", os.path.join(core.CODE_DIR, "lectern_sync.py")] + list(urls or [])
        _proc["p"] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _proc["what"] = what
        return True


def load_state(out):
    sp = os.path.join(out or "", ".sync-state.json")
    if not out or not os.path.exists(sp):
        return {}
    try:
        with open(sp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def rel(p):
    return os.path.relpath(p, core.OUTPUT_ROOT)


def file_url(path):
    return "/files/" + urllib.parse.quote(rel(path))


def docs_info():
    reg = core._load_registry()
    out = []
    for host, guid, folder in core.read_links():
        d = reg.get(guid)
        st = load_state(d)
        pages = [(g, v) for g, v in st.get("pages", {}).items() if v.get("path") and not v.get("removed")]
        pages.sort(key=lambda x: x[1]["path"])
        removed = sum(1 for v in st.get("pages", {}).values() if v.get("removed"))
        pdf = os.path.join(d, st["pdf_name"]) if d and st.get("pdf_name") else None
        out.append({
            "guid": guid, "url": "https://%s/share/d/%s" % (host, guid),
            "title": st.get("title") or None, "folder": rel(d) if d else (folder or ""),
            "configured_folder": folder or "", "pages": len(pages), "removed": removed,
            "last_sync": st.get("last_sync"),
            "pdf": file_url(pdf) if pdf and os.path.exists(pdf) else None,
            "read": "/site/index.html#/%s/%s" % (guid, pages[0][0]) if pages else None,
            "changes": file_url(os.path.join(d, "CHANGES.md")) if d and os.path.exists(os.path.join(d, "CHANGES.md")) else None,
        })
    return out


def tail_log(n=200):
    try:
        with open(core.LOG_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-n:]
    except FileNotFoundError:
        lines = []
    return [ln.replace(core.OUTPUT_ROOT + "/", "") for ln in lines]


def add_links(urls, folder):
    lines = cli.read_lines()
    added = []
    for u in urls:
        p = core.parse_link(u)
        if not p:
            continue
        i, existing = cli.find_line(lines, p[1])
        if existing:
            continue
        url = "https://%s/share/d/%s" % (p[0], p[1])
        lines.append(url + (" -> " + folder if folder else ""))
        added.append(url)
    if added:
        cli.write_lines(lines)
    return added


def search(q, limit=60):
    terms = [t.lower() for t in q.split() if t.strip()]
    if not terms:
        return []
    reg = core._load_registry()
    results = []
    for _h, g, _f in core.read_links():
        folder = reg.get(g)
        if not folder or not os.path.isdir(folder):
            continue
        st = load_state(folder)
        titles = {v["path"]: (gid, v.get("title")) for gid, v in st.get("pages", {}).items() if v.get("path")}
        for dirpath, dirnames, files in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "assets")
            for fn in sorted(files):
                if not fn.endswith(".md") or fn in ("README.md", "CHANGES.md"):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                low = text.lower()
                if not all(t in low for t in terms):
                    continue
                snippets = []
                for ln in text.splitlines():
                    l = ln.lower()
                    if ln.startswith("<!--") or not any(t in l for t in terms):
                        continue
                    k = min(l.find(t) for t in terms if t in l)
                    s = ln.strip()
                    if len(s) > 200:
                        s = ("…" if k > 70 else "") + ln.strip()[max(0, k - 70):k + 130] + "…"
                    snippets.append(s)
                    if len(snippets) >= 3:
                        break
                page_id, title = titles.get(os.path.relpath(p, folder), (None, fn[:-3]))
                score = sum(low.count(t) for t in terms) + (20 if any(t in (title or "").lower() for t in terms) else 0)
                results.append({"title": title, "doc": st.get("title"), "path": rel(p), "file": file_url(p),
                                "read": "/site/index.html#/%s/%s" % (g, page_id) if page_id else None,
                                "snippets": snippets, "score": score})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


def folder_suggestions():
    out = []
    root = core.OUTPUT_ROOT
    try:
        for a in sorted(os.listdir(root)):
            pa = os.path.join(root, a)
            if a.startswith(".") or not os.path.isdir(pa):
                continue
            out.append(a)
            for b in sorted(os.listdir(pa)):
                pb = os.path.join(pa, b)
                if not b.startswith(".") and os.path.isdir(pb) and b not in ("assets", "Docs"):
                    out.append(a + "/" + b)
    except OSError:
        pass
    return out[:400]


# ----------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "Lectern"

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- responses
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, ctype="text/html; charset=utf-8", code=200):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def err(self, msg, code=400):
        self.send_json({"error": msg}, code)

    # -- GET
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        try:
            if path in ("/", "/index.html"):
                return self.send_text(APP_HTML)
            if path == "/api/state":
                return self.send_json(self.state())
            if path == "/api/log":
                return self.send_json({"lines": tail_log(int((qs.get("n") or ["200"])[0])), "running": sync_running()})
            if path == "/api/search":
                return self.send_json({"results": search((qs.get("q") or [""])[0])})
            if path == "/api/news":
                text = ""
                if os.path.exists(core.WHATS_NEW_FILE):
                    with open(core.WHATS_NEW_FILE, encoding="utf-8") as f:
                        text = f.read()
                return self.send_json({"markdown": text})
            if path == "/api/history":
                import history
                rows = []
                for ln in (history.log_lines(60) or "").splitlines():
                    parts = ln.split("  ", 2)
                    if len(parts) == 3:
                        rows.append({"sha": parts[0], "date": parts[1], "message": parts[2]})
                return self.send_json({"snapshots": rows})
            m = re.fullmatch(r"/api/history/([0-9a-f]{4,40})", path)
            if m:
                import history
                stat = history.git("show", "--stat", "--pretty=format:", m.group(1), check=False)
                diff = history.git("show", "--pretty=format:", "--no-color", m.group(1), "--", "*.md", check=False)
                if len(diff) > 400000:
                    diff = diff[:400000] + "\n… (truncated)"
                return self.send_json({"stat": stat.strip(), "diff": diff})
            if path == "/api/folders":
                return self.send_json({"folders": folder_suggestions()})
            if path in ("/site", "/site/"):
                return self.redirect("/site/index.html")
            if path.startswith("/site/"):
                return self.serve_site(urllib.parse.unquote(path[len("/site/"):]))
            if path.startswith("/files/"):
                return self.serve_file(urllib.parse.unquote(path[len("/files/"):]))
            if path in ("/favicon.ico", "/apple-touch-icon.png", "/manifest.webmanifest"):
                return self.serve_static(path[1:])
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            return self.err("not found", 404)
        except Exception as e:  # keep the UI alive
            return self.err("server error: %r" % e, 500)

    def do_HEAD(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/files/"):
            return self.serve_file(urllib.parse.unquote(path[len("/files/"):]), body=False)
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):], body=False)
        if path.startswith("/site/"):
            return self.serve_site(urllib.parse.unquote(path[len("/site/"):]), body=False)
        self.send_response(200 if path in ("/", "/index.html") or path.startswith("/api/") else 404)
        self.end_headers()

    def serve_static(self, name, body=True):
        """App icons and manifest shipped in app/static."""
        if name not in STATIC_FILES:
            return self.err("not found", 404)
        with open(os.path.join(core.CODE_DIR, "static", name), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.end_headers()

    def serve_site(self, name, body=True):
        """The docs site Lectern generates (data/site)."""
        if name not in ("index.html", "data.js", "icon.svg", "favicon.ico"):
            return self.err("not found", 404)
        full = os.path.join(core.SITE_DIR, name)
        if not os.path.isfile(full):
            return self.err("The docs site hasn't been built yet. Run a sync.", 404)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def serve_file(self, relpath, body=True):
        root = os.path.realpath(core.OUTPUT_ROOT)
        full = os.path.realpath(os.path.join(root, relpath))
        if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
            return self.err("not found", 404)
        ext = os.path.splitext(full)[1].lower()
        size = os.path.getsize(full)
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not body:
            return
        with open(full, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def state(self):
        sch = cli.get_schedule()
        nd = cli.next_due(sch)
        return {
            "docs": docs_info(),
            "schedule": sch, "schedule_text": cli.describe_schedule(sch),
            "next_run": nd.strftime("%a %d %b, %H:%M") if nd else None,
            "config": core.load_config(), "running": sync_running(),
            "site": "/site/index.html" if os.path.exists(os.path.join(core.SITE_DIR, "index.html")) else None,
            "news": os.path.exists(core.WHATS_NEW_FILE),
            "root": os.environ.get("LECTERN_HOST_ROOT") or core.OUTPUT_ROOT,
        }

    # -- POST
    def do_POST(self):
        # Only accept requests from this UI's own page (blocks other websites in your browser).
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urllib.parse.urlsplit(origin).netloc != host:
            return self.err("cross-origin request refused", 403)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.err("expected JSON", 415)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 5_000_000)) or b"{}")
        except Exception:
            return self.err("bad JSON")
        try:
            return self.post(urllib.parse.urlsplit(self.path).path, body)
        except Exception as e:
            return self.err("server error: %r" % e, 500)

    def post(self, path, b):
        if path == "/api/sync":
            guid = b.get("guid")
            urls, what = None, "Syncing all docs"
            if guid:
                hit = [l for l in core.read_links() if l[1] == guid]
                if not hit:
                    return self.err("unknown doc")
                urls, what = ["https://%s/share/d/%s" % (hit[0][0], guid)], "Syncing one doc"
            ok = start_sync(urls, what)
            return self.send_json({"started": ok, "running": sync_running()})

        if path == "/api/links/add":
            p = core.parse_link(b.get("url") or "")
            if not p:
                return self.err("That isn't a bitdocs share link (…bitdocs.ai/share/d/…).")
            if any(g == p[1] for _, g, _ in core.read_links()):
                return self.err("That doc is already in your list.")
            title = cli.fetch_title(p[0], p[1])
            if title is None:
                return self.err("That link doesn't exist or isn't shared publicly.")
            folder = clean_folder(b.get("folder"))
            added = add_links([b["url"]], folder)
            started = start_sync(added, "Downloading %s" % title)
            return self.send_json({"title": title, "added": added, "started": started})

        if path == "/api/links/move":
            folder = clean_folder(b.get("folder"))
            if not folder:
                return self.err("Choose a folder.")
            lines = cli.read_lines()
            i, existing = cli.find_line(lines, b.get("guid") or "")
            if existing is None:
                return self.err("unknown doc")
            url = "https://%s/share/d/%s" % (existing[0], existing[1])
            lines[i] = url + " -> " + folder
            cli.write_lines(lines)
            started = start_sync([url], "Moving files")
            return self.send_json({"ok": True, "started": started})

        if path == "/api/links/remove":
            lines = cli.read_lines()
            i, existing = cli.find_line(lines, b.get("guid") or "")
            if existing is None:
                return self.err("unknown doc")
            lines.pop(i)
            cli.write_lines(lines)
            return self.send_json({"ok": True})

        if path == "/api/scan":
            text = (b.get("text") or "").replace("&amp;", "&")
            known = {g for _, g, _ in core.read_links()}
            seen, found = set(), []
            for m in cli.LINK_RE.findall(text):
                p = core.parse_link(m)
                if not p or p[1] in seen:
                    continue
                seen.add(p[1])
                title = None if p[1] in known else cli.fetch_title(p[0], p[1])
                found.append({"url": "https://%s/share/d/%s" % (p[0], p[1]), "guid": p[1],
                              "status": "added" if p[1] in known else ("missing" if title is None else "new"),
                              "title": title})
            return self.send_json({"found": found})

        if path == "/api/scan/add":
            added = add_links(b.get("urls") or [], clean_folder(b.get("folder")))
            started = start_sync(added, "Downloading %d new doc(s)" % len(added)) if added else False
            return self.send_json({"added": added, "started": started})

        if path == "/api/schedule":
            mode = b.get("mode")
            if mode == "every":
                hours = float(b.get("hours") or 0)
                if hours < 1:
                    return self.err("At least 1 hour.")
                sch = {"mode": "every", "hours": hours, "enabled": bool(b.get("enabled", True))}
            elif mode in ("daily", "weekdays"):
                times = []
                for t in b.get("times") or []:
                    if not re.fullmatch(r"\d{1,2}:\d{2}", t or ""):
                        return self.err("Times look like 09:00.")
                    h, m = map(int, t.split(":"))
                    if h > 23 or m > 59:
                        return self.err("Times look like 09:00.")
                    times.append("%02d:%02d" % (h, m))
                sch = {"mode": mode, "times": sorted(set(times)) or ["09:00"], "enabled": bool(b.get("enabled", True))}
            else:
                return self.err("unknown schedule mode")
            cli.set_schedule(sch)
            core.log("Schedule changed from the web UI: %s" % cli.describe_schedule(sch))
            return self.send_json({"schedule": sch, "text": cli.describe_schedule(sch)})

        if path == "/api/config":
            key = b.get("key")
            if key not in core.DEFAULT_CONFIG:
                return self.err("unknown option")
            cfg = core.load_config()
            cfg[key] = bool(b.get("value"))
            core.save_config(cfg)
            return self.send_json({"config": cfg})

        if path == "/api/pdf":
            guid = b.get("guid")

            def work():
                import docsite
                docsite.build_pdf(guid, force=True)
            if sync_running():
                return self.err("A sync is running; try again when it's done.")
            threading.Thread(target=work, daemon=True).start()
            return self.send_json({"started": True})

        return self.err("not found", 404)


def clean_folder(f):
    f = (f or "").strip().strip("/")
    if not f:
        return None
    if ".." in f.split("/"):
        raise ValueError("folder can't contain ..")
    return f


def serve(host="127.0.0.1", port=8790):
    httpd = ThreadingHTTPServer((host, port), Handler)
    core.log("Web UI listening on port %d" % port)
    httpd.serve_forever()


# ------------------------------------------------------------------- app
APP_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lectern</title>
<link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#c2581b">
<style>
:root{--bg:#f7f6f3;--panel:#fff;--soft:#f0eee9;--text:#1d2125;--muted:#6b7075;--line:#e4e1da;--accent:#c2581b;
--accent-ink:#fff;--accent-soft:#f8e8dc;--ok:#2e7d4f;--ok-soft:#e3f2e8;--warn:#a86b12;--bad:#b3392f;--bad-soft:#f9e3e0;
--shadow:0 1px 2px rgba(20,20,20,.05),0 2px 8px rgba(20,20,20,.04);--code:#f2f0eb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141619;--panel:#1b1e22;--soft:#22262b;
--text:#e7e7e4;--muted:#9aa0a6;--line:#2d3237;--accent:#f0915a;--accent-ink:#1b1e22;--accent-soft:#3a2a20;
--ok:#6cc596;--ok-soft:#1f3328;--warn:#e0b152;--bad:#ef7a6c;--bad-soft:#3a2220;--shadow:none;--code:#202428}}
:root[data-theme="dark"]{--bg:#141619;--panel:#1b1e22;--soft:#22262b;--text:#e7e7e4;--muted:#9aa0a6;--line:#2d3237;
--accent:#f0915a;--accent-ink:#1b1e22;--accent-soft:#3a2a20;--ok:#6cc596;--ok-soft:#1f3328;--warn:#e0b152;
--bad:#ef7a6c;--bad-soft:#3a2220;--shadow:none;--code:#202428}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,"Noto Sans",sans-serif}
a{color:var(--accent)}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
.bar{max-width:1100px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.logo{font-weight:700;font-size:1.1em;display:flex;align-items:center;gap:8px}
.logo img{width:28px;height:28px;border-radius:7px;display:block}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:.82em;padding:3px 10px;border-radius:99px;background:var(--soft);color:var(--muted)}
.pill.busy{background:var(--accent-soft);color:var(--accent)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor}
.pill.busy .dot{animation:pulse 1s infinite}@keyframes pulse{50%{opacity:.3}}
.grow{flex:1}
nav.tabs{max-width:1100px;margin:0 auto;padding:0 20px;display:flex;gap:2px;overflow-x:auto}
nav.tabs button{background:none;border:0;border-bottom:2px solid transparent;color:var(--muted);padding:9px 12px;font:inherit;cursor:pointer;white-space:nowrap}
nav.tabs button.on{color:var(--text);border-color:var(--accent);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:22px 20px 60px}
.btn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:8px;padding:6px 12px;font:inherit;font-size:.9em;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn:hover{border-color:var(--muted)}.btn:disabled{opacity:.5;cursor:default}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
.btn.small{padding:3px 9px;font-size:.82em}.btn.danger{color:var(--bad)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:16px 18px;margin-bottom:16px}
h2{font-size:1.05em;margin:0 0 12px}h3{font-size:.95em;margin:18px 0 8px}
.muted{color:var(--muted)}.small{font-size:.85em}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.stat b{display:block;font-size:1.5em;line-height:1.2}.stat span{color:var(--muted);font-size:.85em}
.doc{display:grid;grid-template-columns:1fr auto;gap:6px 16px;padding:14px 0;border-top:1px solid var(--line)}
.doc:first-child{border-top:0;padding-top:4px}
.doc .t{font-weight:600}.doc .f{color:var(--muted);font-size:.85em;word-break:break-word}
.doc .acts{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;align-items:start}
.group{font-size:.75em;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin:18px 0 2px}
.group:first-child{margin-top:0}
input[type=text],input[type=search],input[type=time],input[type=number],textarea,select{width:100%;padding:8px 11px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit}
textarea{min-height:120px;resize:vertical;font-size:.9em}
label{display:block;font-size:.85em;font-weight:600;margin:10px 0 4px}
.row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}.row>*{flex:1;min-width:200px}.row>.btn{flex:0}
.log{background:var(--code);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:12px/1.5 ui-monospace,Menlo,"DejaVu Sans Mono",monospace;max-height:420px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.log .e{color:var(--bad)}.log .w{color:var(--warn)}.log .n{color:var(--ok)}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--text);color:var(--bg);padding:9px 16px;border-radius:10px;font-size:.9em;opacity:0;transition:opacity .2s;pointer-events:none;z-index:20;max-width:90vw}
.toast.show{opacity:1}.toast.bad{background:var(--bad);color:#fff}
.found{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line)}
.tag{font-size:.75em;padding:2px 8px;border-radius:99px;background:var(--soft);color:var(--muted)}
.tag.new{background:var(--ok-soft);color:var(--ok)}.tag.missing{background:var(--bad-soft);color:var(--bad)}
.result{padding:12px 0;border-top:1px solid var(--line)}.result:first-child{border-top:0}
.result .s{color:var(--muted);font-size:.88em;margin-top:3px}
mark{background:var(--accent-soft);color:inherit;border-radius:3px;padding:0 1px}
.news h2{margin-top:22px;font-size:1.05em}.news h3{margin:14px 0 4px}.news ul{margin:4px 0;padding-left:20px}
.news pre,.diff{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;font:12px/1.45 ui-monospace,Menlo,monospace}
.diff .a{color:var(--ok)}.diff .d{color:var(--bad)}.diff .h{color:var(--muted)}
details summary{cursor:pointer;color:var(--muted);font-size:.88em}
.snap{display:flex;gap:12px;padding:9px 0;border-top:1px solid var(--line);align-items:baseline;cursor:pointer}
.snap:first-child{border-top:0}.snap code{color:var(--accent)}
.switch{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--line)}
.switch:first-of-type{border-top:0}
.switch input{width:38px;height:22px;appearance:none;background:var(--line);border-radius:99px;position:relative;cursor:pointer;flex:none;transition:background .15s}
.switch input::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .15s}
.switch input:checked{background:var(--accent)}.switch input:checked::after{left:19px}
.times{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.times input{width:auto}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:var(--panel);color:var(--muted);padding:6px 12px;font:inherit;font-size:.9em;cursor:pointer}
.seg button.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.empty{text-align:center;padding:30px;color:var(--muted)}
@media (max-width:640px){.doc{grid-template-columns:1fr}.doc .acts{justify-content:flex-start}.bar{padding:10px 16px}main{padding:16px}}
</style></head><body>
<header><div class="bar">
  <div class="logo"><img src="/static/icon.svg" alt="">Lectern</div>
  <span id="status" class="pill"><span class="dot"></span><span>Idle</span></span>
  <span class="grow"></span>
  <a id="siteBtn" class="btn" target="_blank" rel="noopener" href="#" hidden>Open docs site ↗</a>
  <button id="syncAll" class="btn primary" type="button">Sync now</button>
  <button id="theme" class="btn" type="button" title="Toggle dark mode">◐</button>
</div>
<nav class="tabs" id="tabs">
  <button data-tab="docs" class="on">Docs</button><button data-tab="add">Add</button><button data-tab="search">Search</button>
  <button data-tab="news">What's new</button><button data-tab="history">History</button>
  <button data-tab="activity">Activity</button><button data-tab="settings">Settings</button>
</nav></header>
<main id="view"></main>
<div id="toast" class="toast"></div>
<datalist id="folders"></datalist>
<script>
(function(){
var S={tab:"docs",state:null,logTimer:null};
var view=document.getElementById("view");
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function api(path,body){var o=body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{};
  return fetch(path,o).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||r.statusText);return j})})}
function toast(msg,bad){var t=document.getElementById("toast");t.textContent=msg;t.className="toast show"+(bad?" bad":"");clearTimeout(t._h);t._h=setTimeout(function(){t.className="toast"},3200)}
function fail(e){toast(e.message||String(e),true)}
var store={get:function(k){try{return localStorage.getItem(k)}catch(e){return null}},set:function(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
var root=document.documentElement,th=store.get("theme");if(th)root.setAttribute("data-theme",th);
document.getElementById("theme").onclick=function(){var dark=root.getAttribute("data-theme")==="dark"||(!root.getAttribute("data-theme")&&matchMedia("(prefers-color-scheme: dark)").matches);
  var n=dark?"light":"dark";root.setAttribute("data-theme",n);store.set("theme",n)};
document.getElementById("tabs").onclick=function(e){var b=e.target.closest("button");if(!b)return;go(b.dataset.tab)};
function go(tab){S.tab=tab;store.set("tab",tab);document.querySelectorAll("#tabs button").forEach(function(b){b.classList.toggle("on",b.dataset.tab===tab)});
  clearInterval(S.logTimer);render()}
document.getElementById("syncAll").onclick=function(){api("/api/sync",{}).then(function(r){toast(r.started?"Sync started":"A sync is already running");refresh()}).catch(fail)};
function setStatus(st){var el=document.getElementById("status"),busy=!!st.running;el.className="pill"+(busy?" busy":"");
  el.lastChild.textContent=busy?(st.running==="sync"?"Syncing…":st.running+"…"):(st.next_run?"Next sync "+st.next_run:"Automatic sync off");
  document.getElementById("syncAll").disabled=busy;var s=document.getElementById("siteBtn");if(st.site){s.hidden=false;s.href=st.site}}
function refresh(){return api("/api/state").then(function(st){var was=S.state&&S.state.running;S.state=st;setStatus(st);
  if(S.tab==="docs"||S.tab==="settings")render(true);if(was&&!st.running)toast("Sync finished")}).catch(function(){})}
setInterval(function(){if(document.visibilityState==="visible")refresh()},4000);
api("/api/folders").then(function(r){document.getElementById("folders").innerHTML=r.folders.map(function(f){return'<option value="'+esc(f)+'">'}).join("")});

function ago(iso){if(!iso)return"never";var d=new Date(iso.replace(" ","T")),m=Math.round((Date.now()-d)/60000);
  if(m<1)return"just now";if(m<60)return m+" min ago";var h=Math.round(m/60);if(h<24)return h+" h ago";return Math.round(h/24)+" d ago"}

function render(auto){var fn={docs:docsView,add:addView,search:searchView,news:newsView,history:historyView,activity:activityView,settings:settingsView}[S.tab]||docsView;fn(auto)}

// ---- Docs
function docsView(){var st=S.state;if(!st){view.innerHTML='<div class="empty">Loading…</div>';return}
  var docs=st.docs,pages=docs.reduce(function(a,d){return a+d.pages},0),last=docs.map(function(d){return d.last_sync}).filter(Boolean).sort().pop();
  var h='<div class="stats"><div class="stat"><b>'+docs.length+'</b><span>docs</span></div><div class="stat"><b>'+pages+'</b><span>pages</span></div>'+
    '<div class="stat"><b>'+esc(ago(last))+'</b><span>last sync</span></div><div class="stat"><b>'+esc(st.next_run||"off")+'</b><span>next sync</span></div></div>';
  if(!docs.length){view.innerHTML=h+'<div class="card empty">No docs yet. <a href="#" id="toAdd">Add your first link</a>.</div>';document.getElementById("toAdd").onclick=function(e){e.preventDefault();go("add")};return}
  var groups={};docs.forEach(function(d){var g=(d.folder||"").split("/")[0]||"Other";(groups[g]=groups[g]||[]).push(d)});
  h+='<div class="card">';Object.keys(groups).forEach(function(g){h+='<div class="group">'+esc(g)+'</div>';
    groups[g].forEach(function(d){h+='<div class="doc"><div><div class="t">'+esc(d.title||"Not synced yet")+'</div>'+
      '<div class="f">'+esc(d.folder)+'</div><div class="small muted">'+d.pages+' pages · synced '+esc(ago(d.last_sync))+(d.removed?' · '+d.removed+' removed online':'')+'</div></div>'+
      '<div class="acts">'+(d.read?'<a class="btn small" target="_blank" rel="noopener" href="'+esc(d.read)+'">Read</a>':'')+
      (d.pdf?'<a class="btn small" target="_blank" rel="noopener" href="'+esc(d.pdf)+'">PDF</a>':'')+
      '<a class="btn small" target="_blank" rel="noopener" href="'+esc(d.url)+'">Online ↗</a>'+
      '<button class="btn small" data-act="sync" data-g="'+d.guid+'">Sync</button>'+
      '<button class="btn small" data-act="pdf" data-g="'+d.guid+'">Rebuild PDF</button>'+
      '<button class="btn small" data-act="move" data-g="'+d.guid+'" data-f="'+esc(d.configured_folder)+'">Move</button>'+
      '<button class="btn small danger" data-act="remove" data-g="'+d.guid+'" data-t="'+esc(d.title||d.url)+'">Remove</button></div></div>'})});
  view.innerHTML=h+'</div>';
  view.querySelectorAll("[data-act]").forEach(function(b){b.onclick=function(){var g=b.dataset.g,a=b.dataset.act;
    if(a==="sync")api("/api/sync",{guid:g}).then(function(r){toast(r.started?"Sync started":"A sync is already running");refresh()}).catch(fail);
    if(a==="pdf")api("/api/pdf",{guid:g}).then(function(){toast("Building PDF… it appears in a minute")}).catch(fail);
    if(a==="move"){var f=prompt("New folder (inside your Courses folder):",b.dataset.f||"");if(f)api("/api/links/move",{guid:g,folder:f}).then(function(){toast("Moving files…");refresh()}).catch(fail)}
    if(a==="remove"&&confirm("Stop syncing “"+b.dataset.t+"”?\n\nThe downloaded files stay where they are."))api("/api/links/remove",{guid:g}).then(function(){toast("Removed. Local files kept.");refresh()}).catch(fail)}})}

// ---- Add
function addView(){view.innerHTML='<div class="card"><h2>Add a doc</h2><div class="row"><div><label for="u">Share link</label><input id="u" type="text" placeholder="https://yourorg.bitdocs.ai/share/d/…"></div>'+
  '<div><label for="f">Folder <span class="muted">(inside your Courses folder)</span></label><input id="f" type="text" list="folders" placeholder="Course Name/Docs"></div>'+
  '<button id="addBtn" class="btn primary" type="button">Add &amp; download</button></div>'+
  '<p class="small muted">Leave the folder empty to use a new folder named after the doc. Several docs in the same folder each get their own subfolder.</p></div>'+
  '<div class="card"><h2>Find links on a course page</h2><p class="small muted">Course pages usually need a login, so paste the page here: open it in your browser, press ⌘A then ⌘C (or View Source and copy), and paste.</p>'+
  '<textarea id="scanText" placeholder="Paste page text or HTML…"></textarea><div style="margin-top:10px"><button id="scanBtn" class="btn" type="button">Find links</button></div><div id="scanOut"></div></div>';
  document.getElementById("addBtn").onclick=function(){var b=this;b.disabled=true;
    api("/api/links/add",{url:document.getElementById("u").value.trim(),folder:document.getElementById("f").value.trim()})
    .then(function(r){toast("Added “"+r.title+"”"+(r.started?", downloading…":". It downloads with the next sync."));document.getElementById("u").value="";refresh()}).catch(fail).then(function(){b.disabled=false})};
  document.getElementById("scanBtn").onclick=function(){var b=this,out=document.getElementById("scanOut");b.disabled=true;out.innerHTML='<p class="muted small">Checking links…</p>';
    api("/api/scan",{text:document.getElementById("scanText").value}).then(function(r){
      if(!r.found.length){out.innerHTML='<p class="muted">No bitdocs share links found.</p>';return}
      var news=r.found.filter(function(x){return x.status==="new"});
      var h='<h3>Found '+r.found.length+' link(s)</h3>'+r.found.map(function(x){return'<div class="found">'+(x.status==="new"?'<input type="checkbox" checked data-u="'+esc(x.url)+'">':'<span style="width:13px"></span>')+
        '<span class="tag '+x.status+'">'+({new:"new",added:"already added",missing:"not available"})[x.status]+'</span><span>'+esc(x.title||x.url)+'</span></div>'}).join("");
      if(news.length)h+='<div class="row" style="margin-top:12px"><div><label for="sf">Folder for the new ones</label><input id="sf" type="text" list="folders" placeholder="Course Name/Docs"></div><button id="scanAdd" class="btn primary" type="button">Add selected</button></div>';
      out.innerHTML=h;var sa=document.getElementById("scanAdd");if(sa)sa.onclick=function(){var urls=[].map.call(out.querySelectorAll("input[type=checkbox]:checked"),function(c){return c.dataset.u});
        api("/api/scan/add",{urls:urls,folder:document.getElementById("sf").value.trim()}).then(function(r){toast("Added "+r.added.length+" doc(s)"+(r.started?", downloading…":""));go("docs");refresh()}).catch(fail)}
    }).catch(fail).then(function(){b.disabled=false})}}

// ---- Search
function searchView(){view.innerHTML='<div class="card"><input id="q" type="search" placeholder="Search all downloaded docs… (all words must match)" autofocus><div id="res" style="margin-top:12px"></div></div>';
  var q=document.getElementById("q"),t;q.value=S.lastQ||"";q.focus();q.oninput=function(){clearTimeout(t);t=setTimeout(run,250)};if(q.value)run();
  function run(){var v=q.value.trim();S.lastQ=v;var res=document.getElementById("res");if(!v){res.innerHTML="";return}
    api("/api/search?q="+encodeURIComponent(v)).then(function(r){var re=new RegExp("("+v.split(/\s+/).map(function(w){return w.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")}).join("|")+")","gi");
      res.innerHTML='<p class="small muted">'+r.results.length+' page(s)</p>'+r.results.map(function(x){return'<div class="result"><a href="'+esc(x.read||x.file)+'" target="_blank" rel="noopener"><strong>'+esc(x.title).replace(re,"<mark>$1</mark>")+'</strong></a> <span class="small muted">· '+esc(x.doc)+'</span>'+
        x.snippets.map(function(s){return'<div class="s">'+esc(s).replace(re,"<mark>$1</mark>")+'</div>'}).join("")+'</div>'}).join("")}).catch(fail)}}

// ---- What's new (tiny markdown renderer for WHATS-NEW.md)
function md(text){var out=[],inCode=false,code=[],lang="";text.split("\n").forEach(function(ln){var t=ln.replace(/^  /,"");
  if(/^```/.test(t)){if(inCode){out.push('<pre class="diff">'+code.map(function(c){var k=c[0]==="+"?"a":c[0]==="-"?"d":c.slice(0,2)==="@@"?"h":"";return'<span class="'+k+'">'+esc(c)+'</span>'}).join("\n")+'</pre>');code=[];inCode=false}else{inCode=true}return}
  if(inCode){code.push(t);return}
  if(/^<\/?details>|^<summary>/.test(t)||/^<details><summary>/.test(t)){out.push(t.replace("show what changed","Show what changed"));return}
  var m;if(m=t.match(/^(#{1,3}) (.*)/)){out.push("<h"+(m[1].length+1)+">"+inline(m[2])+"</h"+(m[1].length+1)+">");return}
  if(m=t.match(/^- (.*)/)){out.push("<ul><li>"+inline(m[1])+"</li></ul>");return}
  if(t.trim())out.push("<p>"+inline(t)+"</p>")});return out.join("")}
function inline(s){return esc(s).replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>").replace(/\*(.+?)\*/g,"<em>$1</em>").replace(/\[([^\]]+)\]\(&lt;(.+?)&gt;\)/g,function(_,t,u){return'<a target="_blank" rel="noopener" href="/files/'+encodeURI(u.replace(/&amp;/g,"&"))+'">'+t+'</a>'})}
function newsView(){view.innerHTML='<div class="card news"><div class="empty">Loading…</div></div>';
  api("/api/news").then(function(r){view.firstChild.innerHTML=r.markdown?md(r.markdown.replace(/^# .*\n+/,"")):'<div class="empty">Nothing yet. This fills in when a sync finds new or changed pages.</div>'}).catch(fail)}

// ---- History
function historyView(){view.innerHTML='<div class="card"><h2>Snapshots</h2><p class="small muted">A snapshot of every doc folder is saved after each sync. Click one to see what changed.</p><div id="snaps"></div></div><div id="snapOut"></div>';
  api("/api/history").then(function(r){var el=document.getElementById("snaps");if(!r.snapshots.length){el.innerHTML='<div class="empty">No snapshots yet.</div>';return}
    el.innerHTML=r.snapshots.map(function(s){return'<div class="snap" data-sha="'+s.sha+'"><code>'+s.sha+'</code><span class="muted small">'+esc(s.date)+'</span><span>'+esc(s.message.replace(/^Sync [\d-]+ [\d:]+( - )?/,"")||"Re-rendered pages")+'</span></div>'}).join("");
    el.onclick=function(e){var s=e.target.closest(".snap");if(!s)return;var out=document.getElementById("snapOut");out.innerHTML='<div class="card"><div class="empty">Loading…</div></div>';
      api("/api/history/"+s.dataset.sha).then(function(d){var lines=d.diff.split("\n");out.innerHTML='<div class="card"><h2>Snapshot '+esc(s.dataset.sha)+'</h2><pre class="diff">'+esc(d.stat)+'</pre><h3>Text changes</h3>'+
        (d.diff.trim()?'<pre class="diff">'+lines.map(function(c){var k=c.startsWith("+")&&!c.startsWith("+++")?"a":c.startsWith("-")&&!c.startsWith("---")?"d":/^(@@|diff |index )/.test(c)?"h":"";return'<span class="'+k+'">'+esc(c)+'</span>'}).join("\n")+'</pre>':'<p class="muted">No text changes (images or re-rendering only).</p>')+
        '<p class="small muted">To restore a file from this snapshot: <code>lectern history restore "PATH" '+esc(s.dataset.sha)+'</code></p></div>';out.scrollIntoView({behavior:"smooth"})}).catch(fail)}}).catch(fail)}

// ---- Activity
function activityView(){view.innerHTML='<div class="card"><div class="row" style="align-items:center"><h2 style="margin:0">Activity</h2><span class="grow"></span></div><div id="log" class="log" style="margin-top:12px">Loading…</div></div>';
  function load(){api("/api/log?n=300").then(function(r){var el=document.getElementById("log");if(!el)return;var atBottom=el.scrollTop+el.clientHeight>=el.scrollHeight-20;
    el.innerHTML=r.lines.map(function(l){var k=/ERROR|Traceback/.test(l)?"e":/WARN/.test(l)?"w":/Done .*: [1-9]|new:|updated:|History:/.test(l)?"n":"";return'<span class="'+k+'">'+esc(l)+'</span>'}).join("\n")||"No activity yet.";
    if(atBottom)el.scrollTop=el.scrollHeight})}
  load();setTimeout(function(){var el=document.getElementById("log");if(el)el.scrollTop=el.scrollHeight},300);S.logTimer=setInterval(load,2000)}

// ---- Settings
function settingsView(auto){var st=S.state;if(!st){view.innerHTML='<div class="empty">Loading…</div>';return}
  if(auto&&(S.editing||(document.activeElement&&view.contains(document.activeElement))))return; // don't re-render while editing
  var sch=st.schedule,c=st.config,mode=sch.mode;
  var h='<div class="card"><h2>Automatic sync</h2><div class="switch"><span>Sync automatically<br><span class="small muted">'+esc(st.schedule_text)+(st.next_run?' · next: '+esc(st.next_run):'')+'</span></span><input type="checkbox" id="en" '+(sch.enabled!==false?"checked":"")+'></div>'+
    '<label>How often</label><div class="seg" id="mode"><button data-m="daily">Every day</button><button data-m="weekdays">Weekdays</button><button data-m="every">Every few hours</button></div>'+
    '<div id="modeBody" style="margin-top:12px"></div><div style="margin-top:14px"><button class="btn primary" id="saveSch" type="button">Save schedule</button></div>'+
    '<p class="small muted">Missed times (Mac asleep, Docker stopped) are caught up as soon as the container is running again.</p></div>'+
    '<div class="card"><h2>After each sync</h2>'+
    sw("site","Rebuild the docs site","Sidebar, search and dark mode, at /site")+sw("pdf","Rebuild PDFs","For docs that changed")+
    sw("git","Save a history snapshot","So you can see or undo changes")+sw("videos","Download uploaded videos","Hosted players like YouTube stay as links")+'</div>';
  view.innerHTML=h;
  function sw(k,t,d){return'<div class="switch"><span>'+t+'<br><span class="small muted">'+d+'</span></span><input type="checkbox" data-k="'+k+'" '+(c[k]?"checked":"")+'></div>'}
  view.querySelectorAll("[data-k]").forEach(function(i){i.onchange=function(){api("/api/config",{key:i.dataset.k,value:i.checked}).then(function(){toast("Saved");refresh()}).catch(fail)}});
  var times=(sch.times||["09:00"]).slice(),hours=sch.hours||6;
  function body(){var b=document.getElementById("modeBody");document.querySelectorAll("#mode button").forEach(function(x){x.classList.toggle("on",x.dataset.m===mode)});
    if(mode==="every"){b.innerHTML='<label for="hrs">Every … hours</label><input id="hrs" type="number" min="1" step="1" value="'+hours+'" style="max-width:120px">';document.getElementById("hrs").oninput=function(){hours=this.value}}
    else{b.innerHTML='<label>At</label><div class="times">'+times.map(function(t,i){return'<input type="time" value="'+t+'" data-i="'+i+'">'+(times.length>1?'<button class="btn small" data-rm="'+i+'" type="button">✕</button>':'')}).join("")+'<button class="btn small" id="addT" type="button">+ Add time</button></div>';
      b.querySelectorAll("input[type=time]").forEach(function(x){x.onchange=function(){times[+x.dataset.i]=x.value}});
      b.querySelectorAll("[data-rm]").forEach(function(x){x.onclick=function(){times.splice(+x.dataset.rm,1);body()}});
      document.getElementById("addT").onclick=function(){times.push("21:00");body()}}}
  S.editing=false;view.oninput=view.onclick=function(e){if(e.target.closest("#modeBody,#mode"))S.editing=true};
  document.getElementById("mode").onclick=function(e){var x=e.target.closest("button");if(x){mode=x.dataset.m;S.editing=true;body()}};body();
  function save(){var en=document.getElementById("en").checked;var p=mode==="every"?{mode:mode,hours:+hours,enabled:en}:{mode:mode,times:times,enabled:en};
    api("/api/schedule",p).then(function(r){toast("Saved: "+r.text);S.editing=false;document.activeElement&&document.activeElement.blur();refresh()}).catch(fail)}
  document.getElementById("saveSch").onclick=save;document.getElementById("en").onchange=save}

var tabs=["docs","add","search","news","history","activity","settings"],hashTab=location.hash.slice(1);
S.tab=tabs.indexOf(hashTab)>=0?hashTab:(store.get("tab")||"docs");go(S.tab);refresh();
})();
</script></body></html>
"""
