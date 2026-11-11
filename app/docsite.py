"""
Local HTML site and PDF export for docs mirrored by lectern_sync.

    build_site()          -> ~/Courses/Docs Site/index.html (sidebar, search, dark mode)
    build_pdf(guid)       -> <doc folder>/<doc title>.pdf  (via headless Google Chrome)
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime

import lectern_sync as core

SITE_DIR = os.path.join(core.OUTPUT_ROOT, "Docs Site")
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
CALLOUT = {"info": "note", "note": "note", "warning": "warning", "error": "danger",
           "danger": "danger", "success": "tip", "tip": "tip"}


# ------------------------------------------------------------------ docs
def list_docs():
    """Docs in links.txt that have been synced: [{guid, out, state}] in links.txt order."""
    reg = core._load_registry()
    docs = []
    for _host, guid, _folder in core.read_links():
        out = reg.get(guid)
        sp = os.path.join(out or "", ".sync-state.json")
        if not out or not os.path.exists(sp):
            continue
        with open(sp, encoding="utf-8") as f:
            st = json.load(f)
        docs.append({"guid": guid, "out": out, "state": st})
    return docs


def doc_pages(doc):
    """Pages of a doc in reading order: [{guid, path, title, depth, body}]."""
    pages = []
    for guid, info in doc["state"].get("pages", {}).items():
        if not info.get("path") or info.get("removed"):
            continue
        raw = os.path.join(doc["out"], ".raw", guid + ".json")
        body = {}
        if os.path.exists(raw):
            with open(raw, encoding="utf-8") as f:
                body = json.load(f).get("body_json") or {}
        path = info["path"]
        depth = path.count(os.sep) - (1 if os.path.basename(path).startswith("00 - ") else 0)
        pages.append({"guid": guid, "path": path, "title": info.get("title") or guid,
                      "depth": max(depth, 0), "body": body})
    pages.sort(key=lambda p: p["path"].replace(os.sep + "00 - ", os.sep + "!"))
    return pages


# -------------------------------------------------------------- renderer
class HtmlRenderer:
    """ProseMirror JSON -> HTML. asset(url) gives the local src; link(href) rewrites page links."""

    def __init__(self, asset, link):
        self.asset, self.link = asset, link

    def render(self, body):
        return self.blocks(body.get("content") or [])

    def blocks(self, nodes):
        return "".join(self.block(n) for n in nodes or [])

    def inline(self, nodes):
        out = []
        for n in nodes or []:
            t = n.get("type")
            if t == "text":
                out.append(self.marks(html.escape(n.get("text", "")), n.get("marks") or []))
            elif t == "hardBreak":
                out.append("<br>")
            else:
                out.append(self.inline(n.get("content")))
        return "".join(out)

    def marks(self, text, marks):
        for m in marks:
            t = m.get("type")
            if t in ("code", "codeInline"):
                text = "<code>%s</code>" % text
            elif t in ("bold", "strong"):
                text = "<strong>%s</strong>" % text
            elif t in ("italic", "em"):
                text = "<em>%s</em>" % text
            elif t in ("strike", "strikethrough"):
                text = "<s>%s</s>" % text
            elif t == "underline":
                text = "<u>%s</u>" % text
            elif t == "highlight":
                text = "<mark>%s</mark>" % text
        for m in marks:
            if m.get("type") == "link":
                href = (m.get("attrs") or {}).get("href") or ""
                if href:
                    href, external = self.link(href)
                    tgt = ' target="_blank" rel="noopener"' if external else ""
                    text = '<a href="%s"%s>%s</a>' % (html.escape(href, True), tgt, text)
        return text

    @staticmethod
    def plain(n):
        if n.get("type") == "text":
            return n.get("text", "")
        return "".join(HtmlRenderer.plain(c) for c in n.get("content") or [])

    def block(self, n):
        t = n.get("type")
        a = n.get("attrs") or {}
        c = n.get("content") or []
        if t == "paragraph":
            inner = self.inline(c)
            return "<p>%s</p>" % inner if inner.strip() else ""
        if t == "heading":
            lvl = max(1, min(int(a.get("level") or 1), 6))
            return '<h%d id="h-%s">%s</h%d>' % (lvl, html.escape(str(a.get("id") or ""), True), self.inline(c), lvl)
        if t in ("codeBlock", "code_block"):
            lang = a.get("language") or ""
            lang = "" if lang in ("null", "plaintext", "auto", None) else lang
            code = html.escape("".join(self.plain(x) for x in c))
            label = '<span class="lang">%s</span>' % html.escape(lang) if lang else ""
            return '<div class="code">%s<button class="copy" type="button">Copy</button><pre><code>%s</code></pre></div>' % (label, code)
        if t == "bulletList":
            return "<ul>%s</ul>" % "".join("<li>%s</li>" % self.blocks(li.get("content")) for li in c)
        if t == "orderedList":
            start = int(a.get("start") or 1)
            return '<ol start="%d">%s</ol>' % (start, "".join("<li>%s</li>" % self.blocks(li.get("content")) for li in c))
        if t == "todoList" and not any("checked" in (li.get("attrs") or {}) for li in c):
            return "<ul>%s</ul>" % "".join("<li>%s</li>" % self.blocks(li.get("content")) for li in c)
        if t in ("taskList", "todoList"):
            items = []
            for li in c:
                chk = "checked" if (li.get("attrs") or {}).get("checked") else ""
                items.append('<li class="task"><input type="checkbox" disabled %s> %s</li>' % (chk, self.blocks(li.get("content"))))
            return '<ul class="tasks">%s</ul>' % "".join(items)
        if t == "blockquote":
            return "<blockquote>%s</blockquote>" % self.blocks(c)
        if t in ("hint", "callout", "callOut", "infoBox", "panel", "alert", "note"):
            kind = CALLOUT.get(str(a.get("type") or "note").lower(), "note")
            return '<div class="callout %s">%s</div>' % (kind, self.blocks(c))
        if t in ("horizontalRule", "divider"):
            return "<hr>"
        if t == "table":
            rows = []
            for i, r in enumerate(c):
                cells = []
                for cell in r.get("content") or []:
                    tag = "th" if cell.get("type") == "tableHeader" or (i == 0 and len(c) > 1) else "td"
                    ca = cell.get("attrs") or {}
                    span = ""
                    if (ca.get("colspan") or 1) > 1:
                        span += ' colspan="%d"' % ca["colspan"]
                    if (ca.get("rowspan") or 1) > 1:
                        span += ' rowspan="%d"' % ca["rowspan"]
                    cells.append("<%s%s>%s</%s>" % (tag, span, self.blocks(cell.get("content")), tag))
                rows.append("<tr>%s</tr>" % "".join(cells))
            return '<div class="table"><table>%s</table></div>' % "".join(rows)
        if t == "atomic":
            return self.atomic(a.get("data") or {})
        if t in ("image",):
            src = a.get("src") or ""
            return '<figure><img src="%s" alt="%s" loading="lazy"></figure>' % (
                html.escape(self.asset(src), True), html.escape(a.get("alt") or "", True)) if src else ""
        if t in ("details",):
            return "<details>%s</details>" % self.blocks(c)
        if t in ("detailsSummary", "summary"):
            return "<summary>%s</summary>" % self.inline(c)
        if c:
            if all(x.get("type") in ("text", "hardBreak") for x in c):
                return "<p>%s</p>" % self.inline(c)
            return self.blocks(c)
        return ""

    def atomic(self, d):
        kind = d.get("kind")
        title = d.get("title") or kind or ""
        if kind == "divider":
            return "<hr>"
        if kind == "image":
            src = self.cdn(d.get("image") or d.get("url"))
            cap = '<figcaption>%s</figcaption>' % html.escape(d["caption"]) if d.get("caption") else ""
            return '<figure><img src="%s" alt="%s" loading="lazy">%s</figure>' % (
                html.escape(self.asset(src), True), html.escape(title, True), cap) if src else ""
        if kind == "file":
            src = self.cdn(d.get("filepath") or d.get("url"))
            size = d.get("filesize")
            size = " · %s" % human_size(size) if size else ""
            return '<p class="file">📎 <a href="%s" download>%s</a><span class="muted">%s</span></p>' % (
                html.escape(self.asset(src), True), html.escape(title), size)
        if kind == "link":
            url = d.get("url") or ""
            desc = html.escape((d.get("description") or "")[:240])
            return ('<a class="card" href="%s" target="_blank" rel="noopener"><strong>%s</strong>'
                    '<span>%s</span><span class="muted">%s</span></a>') % (
                html.escape(url, True), html.escape(title), desc, html.escape(d.get("domain") or ""))
        if kind in core.VIDEO_KINDS:
            fp = self.cdn(d.get("filepath") or d.get("file"))
            url = d.get("url") or d.get("src") or ""
            if fp:
                return '<figure><video controls preload="metadata" src="%s"></video><figcaption>%s</figcaption></figure>' % (
                    html.escape(self.asset(fp), True), html.escape(title))
            if url:
                return '<p class="file">🎬 <a href="%s" target="_blank" rel="noopener">%s</a></p>' % (
                    html.escape(url, True), html.escape(title or url))
        return ""

    @staticmethod
    def cdn(p):
        if not p:
            return ""
        return p if p.startswith("http") else core.UPLOADS_URL + "/" + p.lstrip("/")


def doc_origin(st):
    """https://<org>.bitdocs.ai for a doc, from the share link it was synced from."""
    m = re.match(r"^(https?://[^/]+)", st.get("source") or "")
    return m.group(1) if m else "https://bitdocs.ai"


def human_size(n):
    try:
        n = float(n)
    except Exception:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f %s" % (n, unit)
        n /= 1024
    return "%.1f TB" % n


def text_of(body):
    return re.sub(r"\s+", " ", HtmlRenderer.plain(body)).strip()


# ------------------------------------------------------------------ site
def build_site():
    docs = list_docs()
    os.makedirs(SITE_DIR, exist_ok=True)
    page_index = {}  # page guid -> (doc guid)
    for d in docs:
        d["pages"] = doc_pages(d)
        for p in d["pages"]:
            page_index[p["guid"]] = d["guid"]

    data = []
    for d in docs:
        st, out = d["state"], d["out"]

        def asset(url, st=st, out=out):
            rel = st.get("assets", {}).get(url)
            if rel:
                return urllib.parse.quote(os.path.relpath(os.path.join(out, rel), SITE_DIR))
            return url

        def link(href, origin=doc_origin(st)):
            m = re.match(r"^(?:https?://[^/]+)?/(?:a/docs|share/d)/([A-Za-z0-9]+)(#.*)?$", href)
            if m and m.group(1) in page_index:
                return "#/%s/%s" % (page_index[m.group(1)], m.group(1)), False
            if href.startswith("/"):
                return origin + href, True
            return href, not href.startswith("#")

        r = HtmlRenderer(asset, link)
        folder = os.path.relpath(out, core.OUTPUT_ROOT)
        course = folder.split(os.sep)[0]
        pdf = os.path.join(out, core.safe_name(st.get("title") or "") + ".pdf")
        data.append({
            "id": d["guid"], "title": st.get("title") or d["guid"], "course": course, "folder": folder,
            "source": st.get("source"), "synced": st.get("last_sync"),
            "pdf": urllib.parse.quote(os.path.relpath(pdf, SITE_DIR)) if os.path.exists(pdf) else None,
            "pages": [{"id": p["guid"], "title": p["title"], "depth": p["depth"],
                       "file": urllib.parse.quote(os.path.relpath(os.path.join(out, p["path"]), SITE_DIR)),
                       "html": r.render(p["body"]), "text": text_of(p["body"])} for p in d["pages"]],
        })
    payload = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "docs": data}
    js = "window.DOCS = %s;\n" % json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    _write(os.path.join(SITE_DIR, "data.js"), js)
    _write(os.path.join(SITE_DIR, "index.html"), SITE_HTML)
    for name in ("icon.svg", "favicon.ico"):  # Lectern's icon as the site's favicon
        shutil.copyfile(os.path.join(core.CODE_DIR, "static", name), os.path.join(SITE_DIR, name))
    return os.path.join(SITE_DIR, "index.html")


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------- PDF
def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    for name in ("chromium", "chromium-browser", "google-chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def build_pdf(guid, force=False):
    doc = next((d for d in list_docs() if d["guid"] == guid), None)
    if not doc:
        return None
    st, out = doc["state"], doc["out"]
    title = st.get("title") or guid
    pdf = os.path.join(out, core.safe_name(title) + ".pdf")
    old_name = st.get("pdf_name")
    if old_name and old_name != os.path.basename(pdf) and os.path.exists(os.path.join(out, old_name)):
        os.remove(os.path.join(out, old_name))  # doc was renamed: drop the PDF with the old name
        force = True
    if os.path.exists(pdf) and not force:
        _remember_pdf(out, os.path.basename(pdf))
        return pdf
    chrome = find_chrome()
    if not chrome:
        core.log("WARN: no Chrome/Chromium/Edge found; skipping PDF for '%s'" % title)
        return None
    pages = doc_pages(doc)
    ids = {p["guid"] for p in pages}

    def asset(url):
        rel = st.get("assets", {}).get(url)
        if rel:
            return "file://" + urllib.parse.quote(os.path.join(out, rel))
        return url

    def link(href, origin=doc_origin(st)):
        m = re.match(r"^(?:https?://[^/]+)?/(?:a/docs|share/d)/([A-Za-z0-9]+)(#.*)?$", href)
        if m and m.group(1) in ids:
            return "#p-" + m.group(1), False
        if href.startswith("/"):
            return origin + href, True
        return href, True

    r = HtmlRenderer(asset, link)
    toc = "".join('<li style="margin-left:%dem"><a href="#p-%s">%s</a></li>' % (
        p["depth"] * 1.2, p["guid"], html.escape(p["title"])) for p in pages)
    body = "".join('<section class="page" id="p-%s">%s</section>' % (p["guid"], r.render(p["body"])) for p in pages)
    doc_html = PDF_HTML.format(title=html.escape(title), toc=toc, body=body,
                               synced=html.escape(st.get("last_sync") or ""),
                               source=html.escape(st.get("source") or ""))
    tmpdir = tempfile.mkdtemp(prefix="lectern-pdf-")
    in_container = sys.platform.startswith("linux")
    try:
        src = os.path.join(tmpdir, "doc.html")
        _write(src, doc_html)
        tmp_pdf = os.path.join(tmpdir, "out.pdf")
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--user-data-dir=" + os.path.join(tmpdir, "profile"),
               "--allow-file-access-from-files", "--no-pdf-header-footer", "--print-to-pdf-no-header",
               "--virtual-time-budget=15000", "--print-to-pdf=" + tmp_pdf, "file://" + src]
        if in_container:
            cmd[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
        if not os.path.exists(tmp_pdf) or os.path.getsize(tmp_pdf) < 1000:
            core.log("WARN: Chrome did not produce a PDF for '%s'" % title)
            return None
        shutil.move(tmp_pdf, pdf)
        _remember_pdf(out, os.path.basename(pdf))
        core.log("PDF: %s" % os.path.relpath(pdf, core.OUTPUT_ROOT))
        return pdf
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _remember_pdf(out, name):
    sp = os.path.join(out, ".sync-state.json")
    with open(sp, encoding="utf-8") as f:
        st = json.load(f)
    if st.get("pdf_name") == name:
        return
    st["pdf_name"] = name
    _write(sp, json.dumps(st, indent=1, ensure_ascii=False))


# ------------------------------------------------------------- templates
BASE_CSS = r"""
:root{--bg:#fbfaf8;--panel:#f3f1ec;--text:#1f2328;--muted:#6a6f76;--line:#e3e0d8;--accent:#c2581b;
--accent-soft:#f6e6da;--code-bg:#f0eee8;--note:#2f6fb0;--warning:#b7791f;--danger:#c0392b;--tip:#2e8b57;
--mark:#ffe58a;--shadow:0 1px 2px rgba(0,0,0,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#16181b;--panel:#1d2024;--text:#e6e6e3;
--muted:#9aa0a6;--line:#2c3035;--accent:#f0915a;--accent-soft:#3a2a20;--code-bg:#22262b;--note:#6aa8e8;
--warning:#e0b152;--danger:#ef7a6c;--tip:#6cc596;--mark:#6b5a12;--shadow:none}}
:root[data-theme="dark"]{--bg:#16181b;--panel:#1d2024;--text:#e6e6e3;--muted:#9aa0a6;--line:#2c3035;
--accent:#f0915a;--accent-soft:#3a2a20;--code-bg:#22262b;--note:#6aa8e8;--warning:#e0b152;--danger:#ef7a6c;
--tip:#6cc596;--mark:#6b5a12;--shadow:none}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,"Noto Sans","DejaVu Sans",sans-serif}
a{color:var(--accent)}
.content h1{font-size:1.9em;line-height:1.25;margin:.2em 0 .6em}
.content h2{font-size:1.4em;margin:1.8em 0 .5em;padding-bottom:.2em;border-bottom:1px solid var(--line)}
.content h3{font-size:1.15em;margin:1.4em 0 .4em}
.content img,.content video{max-width:100%;height:auto;border-radius:8px;border:1px solid var(--line)}
figure{margin:1.2em 0}figcaption{color:var(--muted);font-size:.9em;margin-top:.3em}
code{font:0.88em/1.5 ui-monospace,SFMono-Regular,Menlo,"DejaVu Sans Mono",monospace;background:var(--code-bg);padding:.1em .35em;border-radius:4px}
.code{position:relative;margin:1em 0}
.code pre{background:var(--code-bg);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow:auto;margin:0}
.code pre code{background:none;padding:0;font-size:.86em}
.code .lang{position:absolute;top:6px;left:12px;font-size:.7em;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.code .lang+.copy+pre{padding-top:26px}
.copy{position:absolute;top:6px;right:8px;font-size:.75em;border:1px solid var(--line);background:var(--panel);color:var(--muted);border-radius:6px;padding:2px 8px;cursor:pointer}
.callout{border-left:4px solid var(--note);background:var(--panel);padding:.6em 1em;border-radius:0 8px 8px 0;margin:1em 0}
.callout.warning{border-color:var(--warning)}.callout.danger{border-color:var(--danger)}.callout.tip{border-color:var(--tip)}
.callout p{margin:.4em 0}
blockquote{margin:1em 0;padding:.2em 1em;border-left:3px solid var(--line);color:var(--muted)}
.table{overflow-x:auto;margin:1em 0}table{border-collapse:collapse;width:100%;font-size:.93em}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--panel)}
td p,th p{margin:.2em 0}
hr{border:0;border-top:1px solid var(--line);margin:2em 0}
.card{display:flex;flex-direction:column;gap:2px;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:.8em 0;text-decoration:none;color:var(--text);background:var(--panel)}
.card strong{color:var(--accent)}.muted{color:var(--muted);font-size:.88em}
.file a{font-weight:600}
mark{background:var(--mark);color:inherit;border-radius:2px}
"""

SITE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lectern · Course Docs</title>
<link rel="icon" href="favicon.ico" sizes="32x32"><link rel="icon" href="icon.svg" type="image/svg+xml">
<style>""" + BASE_CSS + r"""
.layout{display:grid;grid-template-columns:300px 1fr;min-height:100vh}
aside{background:var(--panel);border-right:1px solid var(--line);position:sticky;top:0;height:100vh;overflow:auto;padding:14px 12px 40px}
.brand{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:2px 4px 12px}
.brand a{color:var(--text);text-decoration:none;font-weight:700;font-size:1.05em}
.iconbtn{border:1px solid var(--line);background:var(--bg);color:var(--text);border-radius:8px;padding:4px 9px;cursor:pointer;font-size:.9em}
#q{width:100%;padding:8px 11px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit;font-size:.95em;margin-bottom:12px}
.course{font-size:.72em;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:16px 6px 4px;font-weight:600}
details.doc>summary{cursor:pointer;padding:5px 6px;border-radius:6px;font-weight:600;font-size:.93em;list-style:none}
details.doc>summary::-webkit-details-marker{display:none}
details.doc>summary::before{content:"▸";display:inline-block;width:1em;color:var(--muted);transition:transform .15s}
details.doc[open]>summary::before{transform:rotate(90deg)}
nav a{display:block;padding:3px 6px 3px 22px;border-radius:6px;color:var(--text);text-decoration:none;font-size:.88em;line-height:1.4}
nav a:hover{background:var(--accent-soft)}nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
main{min-width:0;padding:28px 48px 80px}
.content{max-width:820px;margin:0 auto}
.crumbs{color:var(--muted);font-size:.85em;margin-bottom:10px;display:flex;flex-wrap:wrap;gap:6px 14px}
.crumbs a{color:var(--muted)}
.pager{display:flex;justify-content:space-between;gap:12px;margin-top:48px;border-top:1px solid var(--line);padding-top:16px}
.pager a{text-decoration:none;max-width:48%}.pager small{display:block;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-top:18px}
.tile{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--panel);text-decoration:none;color:var(--text);box-shadow:var(--shadow)}
.tile strong{display:block;margin-bottom:4px}
.result{display:block;padding:12px 0;border-bottom:1px solid var(--line);text-decoration:none;color:var(--text)}
.result strong{color:var(--accent)}.result p{margin:.3em 0 0;color:var(--muted);font-size:.92em}
#menu{display:none}
@media (max-width:860px){.layout{grid-template-columns:1fr}
aside{position:fixed;inset:0 auto 0 0;width:min(86vw,320px);z-index:5;transform:translateX(-100%);transition:transform .2s;box-shadow:0 0 30px rgba(0,0,0,.25)}
body.nav-open aside{transform:none}main{padding:64px 16px 60px}
#menu{display:block;position:fixed;top:12px;left:12px;z-index:6}}
</style></head>
<body>
<button id="menu" class="iconbtn" type="button" aria-label="Menu">☰</button>
<div class="layout">
<aside><div class="brand"><a href="#/" style="display:flex;align-items:center;gap:8px"><img src="icon.svg" alt="" width="24" height="24" style="border-radius:6px">Lectern</a><button id="theme" class="iconbtn" type="button" title="Toggle dark mode">◐</button></div>
<input id="q" type="search" placeholder="Search all docs…" autocomplete="off"><nav id="nav"></nav></aside>
<main><div class="content" id="view"></div></main></div>
<script src="data.js"></script>
<script>
(function(){
var D=window.DOCS||{docs:[]},docs=D.docs,byId={},pageById={};
docs.forEach(function(d){byId[d.id]=d;d.pages.forEach(function(p,i){pageById[p.id]={doc:d,page:p,i:i};});});
var store={get:function(k){try{return localStorage.getItem(k)}catch(e){return null}},set:function(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
var root=document.documentElement,t=store.get("theme");if(t)root.setAttribute("data-theme",t);
document.getElementById("theme").onclick=function(){var dark=root.getAttribute("data-theme")==="dark"||(!root.getAttribute("data-theme")&&matchMedia("(prefers-color-scheme: dark)").matches);
var n=dark?"light":"dark";root.setAttribute("data-theme",n);store.set("theme",n);};
document.getElementById("menu").onclick=function(){document.body.classList.toggle("nav-open");};
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
// sidebar
var nav=document.getElementById("nav"),h="",last=null;
docs.forEach(function(d){if(d.course!==last){h+='<div class="course">'+esc(d.course)+'</div>';last=d.course;}
h+='<details class="doc" data-doc="'+d.id+'"><summary>'+esc(d.title)+'</summary>';
d.pages.forEach(function(p){h+='<a href="#/'+d.id+'/'+p.id+'" data-page="'+p.id+'" style="padding-left:'+(22+p.depth*14)+'px">'+esc(p.title)+'</a>';});
h+='</details>';});nav.innerHTML=h;
var view=document.getElementById("view");
function home(){var s='<h1>Course Docs</h1><p class="muted">'+docs.length+' docs · '+docs.reduce(function(a,d){return a+d.pages.length},0)+' pages · generated '+esc(D.generated)+'</p><div class="grid">';
docs.forEach(function(d){s+='<a class="tile" href="#/'+d.id+'/'+(d.pages[0]?d.pages[0].id:"")+'"><strong>'+esc(d.title)+'</strong><span class="muted">'+esc(d.folder)+'<br>'+d.pages.length+' pages · synced '+esc((d.synced||"").replace("T"," ").slice(0,16))+'</span></a>';});
view.innerHTML=s+'</div>';}
function page(d,p){var e=pageById[p.id],prev=d.pages[e.i-1],next=d.pages[e.i+1];
var s='<div class="crumbs"><span>'+esc(d.course)+'</span><span>'+esc(d.title)+'</span><a href="'+p.file+'">Open .md</a>'+(d.pdf?'<a href="'+d.pdf+'">PDF</a>':'')+(d.source?'<a href="'+esc(d.source)+'" target="_blank" rel="noopener">Online</a>':'')+'</div>'+p.html;
s+='<div class="pager">'+(prev?'<a href="#/'+d.id+'/'+prev.id+'"><small>← Previous</small>'+esc(prev.title)+'</a>':'<span></span>')+(next?'<a href="#/'+d.id+'/'+next.id+'" style="text-align:right"><small>Next →</small>'+esc(next.title)+'</a>':'')+'</div>';
view.innerHTML=s;if(!/<h1/.test(p.html.slice(0,200)))view.insertAdjacentHTML("afterbegin","");}
function mark(){nav.querySelectorAll("a.on").forEach(function(a){a.classList.remove("on")});}
function route(){var m=location.hash.match(/^#\/([^\/]+)\/([^\/?]+)/);mark();document.body.classList.remove("nav-open");
if(m&&pageById[m[2]]){var e=pageById[m[2]];page(e.doc,e.page);var a=nav.querySelector('a[data-page="'+m[2]+'"]');
if(a){a.classList.add("on");a.closest("details").open=true;a.scrollIntoView({block:"nearest"});}document.title=e.page.title+" · Lectern";}
else{home();document.title="Lectern · Course Docs";}window.scrollTo(0,0);}
window.addEventListener("hashchange",route);
// search
var q=document.getElementById("q"),timer;
q.addEventListener("input",function(){clearTimeout(timer);timer=setTimeout(search,120);});
q.addEventListener("keydown",function(e){if(e.key==="Escape"){q.value="";route();}});
function search(){var v=q.value.trim().toLowerCase();if(!v){route();return;}var terms=v.split(/\s+/),res=[];
docs.forEach(function(d){d.pages.forEach(function(p){var tl=p.title.toLowerCase(),tx=p.text.toLowerCase(),score=0;
for(var i=0;i<terms.length;i++){var inT=tl.indexOf(terms[i])>=0,inX=tx.indexOf(terms[i])>=0;if(!inT&&!inX)return;score+=(inT?10:0)+(inX?1:0);}
var at=tx.indexOf(terms[0]),from=Math.max(0,at-80),snip=p.text.slice(from,from+240);res.push({d:d,p:p,s:score,snip:(from?"…":"")+snip+"…"});});});
res.sort(function(a,b){return b.s-a.s});var re=new RegExp("("+terms.map(function(t){return t.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")}).join("|")+")","gi");
var s='<h1>Search</h1><p class="muted">'+res.length+' page(s) match “'+esc(q.value)+'”</p>';
res.slice(0,100).forEach(function(r){s+='<a class="result" href="#/'+r.d.id+'/'+r.p.id+'"><strong>'+esc(r.p.title).replace(re,"<mark>$1</mark>")+'</strong> <span class="muted">· '+esc(r.d.title)+'</span><p>'+esc(r.snip).replace(re,"<mark>$1</mark>")+'</p></a>';});
view.innerHTML=s;}
document.addEventListener("click",function(e){var b=e.target.closest(".copy");if(!b)return;var code=b.parentNode.querySelector("code").innerText;
(navigator.clipboard?navigator.clipboard.writeText(code):Promise.reject()).then(function(){b.textContent="Copied";setTimeout(function(){b.textContent="Copy"},1200);},function(){});});
document.addEventListener("keydown",function(e){if(e.key==="/"&&document.activeElement!==q){e.preventDefault();q.focus();}});
route();
})();
</script></body></html>
"""

PDF_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>""" + BASE_CSS.replace("{", "{{").replace("}", "}}") + r"""
:root{{--bg:#fff}}
body{{font-size:11pt;line-height:1.55}}
@page{{size:A4;margin:16mm 15mm 18mm}}
.cover{{height:240mm;display:flex;flex-direction:column;justify-content:center}}
.cover h1{{font-size:28pt;margin:0 0 8pt;border:0}}
.toc{{page-break-before:always}}.toc ol{{list-style:none;padding:0}}.toc li{{margin:3pt 0}}
.toc a{{color:var(--text);text-decoration:none}}
.page{{page-break-before:always}}
.content img{{max-height:200mm}}
.copy{{display:none}}
pre{{white-space:pre-wrap;word-break:break-word}}
h2,h3{{page-break-after:avoid}}figure,.code,table{{page-break-inside:avoid}}
</style></head><body><div class="content">
<div class="cover"><h1>{title}</h1><p class="muted">Synced {synced}<br>{source}</p></div>
<div class="toc"><h2>Contents</h2><ol>{toc}</ol></div>
{body}
</div></body></html>
"""
