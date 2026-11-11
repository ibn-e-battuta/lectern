#!/usr/bin/env python3
"""
Mirror bit.ai / bitdocs.ai shared docs to local Markdown, and keep them in sync.

Usage:
    python3 lectern_sync.py              # sync every link in links.txt
    python3 lectern_sync.py URL [URL..]  # sync just these links

links.txt format (one per line, '#' comments allowed):
    https://yourorg.bitdocs.ai/share/d/AbCdEf1234567890
    https://yourorg.bitdocs.ai/share/d/XXXX  ->  Some Folder Name

Without "-> folder", output goes to <OUTPUT_ROOT>/<doc title>.
A relative folder is resolved against OUTPUT_ROOT (the Courses folder).

Only Python 3.9 standard library is used.
"""
import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
# LECTERN_ROOT: where docs are written (your ~/Courses; /courses in Docker)
# LECTERN_DATA: links.txt, config, logs, history (the project's data/ folder; /data in Docker)
HERE = os.path.abspath(os.environ.get("LECTERN_DATA") or os.path.join(os.path.dirname(CODE_DIR), "data"))
OUTPUT_ROOT = os.path.abspath(os.environ.get("LECTERN_ROOT") or os.path.expanduser("~/Courses"))
LINKS_FILE = os.path.join(HERE, "links.txt")
LOG_FILE = os.path.join(HERE, "sync.log")
LOCK_FILE = os.path.join(HERE, ".lock")
REGISTRY_FILE = os.path.join(HERE, "registry.json")  # doc id -> folder it was last synced to
CONFIG_FILE = os.path.join(HERE, "config.json")
WHATS_NEW_FILE = os.path.join(OUTPUT_ROOT, "WHATS-NEW.md")
DEFAULT_CONFIG = {"site": True, "pdf": True, "git": True, "videos": True}
UA = "Mozilla/5.0 (Macintosh) lectern/1.0"
DELAY = 0.4  # seconds between requests, be polite
UPLOADS_URL = "https://d3jwyy9rhyhl55.cloudfront.net"  # bit.ai uploads CDN (from the page's BOOTSTRAP config)
CONVERTER_VERSION = 6  # bump to force re-render of every page after converter changes

UNKNOWN_TYPES = set()
VIDEO_KINDS = ("video", "audio", "embed", "youtube", "vimeo", "loom", "wistia", "iframe")


# ----------------------------------------------------------------- helpers
def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http_get(url, binary=False, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return data if binary else data.decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
            last = e  # noqa


def download_to(url, dest, retries=3):
    """Stream a file to disk (works for large videos); atomic rename at the end."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as f:
                shutil.copyfileobj(r, f, 1024 * 1024)
            os.replace(dest + ".part", dest)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if os.path.exists(dest + ".part"):
                os.remove(dest + ".part")
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)


def safe_name(s, maxlen=90):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", s or "Untitled")
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .-")
    return s or "Untitled"


def parse_link(line):
    m = re.search(r"https?://([^/\s]+)/share/d/([A-Za-z0-9]+)", line)
    if not m:
        return None
    host, guid = m.group(1), m.group(2)
    folder = None
    if "->" in line:
        folder = line.split("->", 1)[1].strip() or None
    return host, guid, folder


def read_links():
    links = []
    if not os.path.exists(LINKS_FILE):
        return links
    with open(LINKS_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            p = parse_link(line)
            if p:
                links.append(p)
            else:
                log("WARN: could not parse link line: %s" % line)
    return links


def notify(title, msg):
    if sys.platform != "darwin":
        return
    script = 'display notification %s with title %s' % (json.dumps(msg), json.dumps(title))
    try:
        subprocess.run(["osascript", "-e", script], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ------------------------------------------------- ProseMirror -> Markdown
class Converter:
    def __init__(self, asset_cb, link_cb=lambda h: h):
        self.asset_cb = asset_cb  # url -> relative local path (or original url)
        self.link_cb = link_cb    # href -> local relative .md path for links to other pages

    # inline -----------------------------------------------------------
    def inline(self, nodes):
        out = []
        for n in nodes or []:
            t = n.get("type")
            if t == "text":
                out.append(self.marked(n.get("text", ""), n.get("marks") or []))
            elif t == "hardBreak":
                out.append("  \n")
            elif t in ("image", "inlineImage"):
                out.append(self.image(n))
            elif t == "mention":
                a = n.get("attrs") or {}
                out.append("@" + str(a.get("label") or a.get("name") or a.get("id") or ""))
            elif t in ("math", "mathInline", "katex", "inlineMath"):
                a = n.get("attrs") or {}
                out.append("$" + str(a.get("latex") or a.get("value") or self.plain(n)) + "$")
            elif t == "emoji":
                a = n.get("attrs") or {}
                out.append(str(a.get("emoji") or a.get("name") or ""))
            else:
                if t:
                    UNKNOWN_TYPES.add(t)
                out.append(self.inline(n.get("content")))
        return "".join(out)

    def marked(self, text, marks):
        types = {m.get("type"): m for m in marks}
        if "code" in types or "codeInline" in types:
            tick = "``" if "`" in text else "`"
            text = "%s%s%s" % (tick, text, tick)
        else:
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            core = text.strip()
            if core:
                if "bold" in types or "strong" in types:
                    core = "**%s**" % core
                if "italic" in types or "em" in types:
                    core = "*%s*" % core
                if "strike" in types or "strikethrough" in types:
                    core = "~~%s~~" % core
                if "underline" in types:
                    core = "<u>%s</u>" % core
            text = lead + core + trail
        if "link" in types:
            href = (types["link"].get("attrs") or {}).get("href") or ""
            if href:
                href = self.link_cb(href)
                if not href.startswith(("http", "mailto:", "#")):
                    href = "<%s>" % href  # local path with spaces
                text = "[%s](%s)" % (text, href)
        for k in types:
            if k not in ("code", "codeInline", "bold", "strong", "italic", "em", "strike",
                         "strikethrough", "underline", "link", "textStyle", "highlight",
                         "color", "fontSize", "subscript", "superscript", "comment"):
                UNKNOWN_TYPES.add("mark:" + str(k))
        return text

    def plain(self, n):
        if n.get("type") == "text":
            return n.get("text", "")
        return "".join(self.plain(c) for c in n.get("content") or [])

    def image(self, n):
        a = n.get("attrs") or {}
        src = a.get("src") or a.get("url") or ""
        alt = (a.get("alt") or a.get("title") or "image").replace("]", "")
        if not src:
            return ""
        return "![%s](%s)" % (alt, urllib.parse.quote(self.asset_cb(src), safe="/:.%-_()"))

    # block ------------------------------------------------------------
    def blocks(self, nodes, indent=""):
        parts = []
        for n in nodes or []:
            s = self.block(n, indent)
            if s is not None and s != "":
                parts.append(s)
        return "\n\n".join(parts)

    def block(self, n, indent=""):
        t = n.get("type")
        a = n.get("attrs") or {}
        c = n.get("content") or []
        if t == "doc":
            return self.blocks(c, indent)
        if t == "paragraph":
            return self.prefix(self.inline(c), indent)
        if t == "heading":
            lvl = int(a.get("level") or 1)
            text = self.inline(c).strip()
            m = re.fullmatch(r"\*\*(.+)\*\*", text)
            if m and "**" not in m.group(1):
                text = m.group(1)  # headings are bold already
            return indent + "#" * max(1, min(lvl, 6)) + " " + text
        if t in ("codeBlock", "code_block"):
            lang = a.get("language") or a.get("lang") or ""
            if lang in ("null", "plaintext", "auto"):
                lang = ""
            code = "".join(self.plain(x) for x in c)
            fence = "````" if "```" in code else "```"
            return self.prefix("%s%s\n%s\n%s" % (fence, lang, code.rstrip("\n"), fence), indent)
        if t in ("bulletList", "orderedList", "taskList", "todoList"):
            start = int(a.get("start") or 1)
            items = []
            for i, li in enumerate(c):
                if t == "orderedList":
                    marker = "%d. " % (start + i)
                elif t == "taskList" or li.get("type") in ("taskItem", "todoItem") or \
                        "checked" in (li.get("attrs") or {}):
                    checked = (li.get("attrs") or {}).get("checked")
                    marker = "- [x] " if checked else "- [ ] "
                else:
                    marker = "- "
                body = self.blocks(li.get("content") or [], "")
                pad = " " * len(marker)
                lines = body.split("\n")
                first = indent + marker + lines[0]
                rest = [(indent + pad + ln) if ln.strip() else "" for ln in lines[1:]]
                items.append("\n".join([first] + rest))
            return "\n".join(items)
        if t == "blockquote":
            inner = self.blocks(c)
            return "\n".join(indent + "> " + ln if ln else indent + ">" for ln in inner.split("\n"))
        if t == "atomic":
            d = a.get("data") or {}
            kind = d.get("kind")
            if kind == "divider":
                return indent + "---"
            if kind in VIDEO_KINDS:
                return self.video(d, indent)
            if kind == "link":
                path = d.get("url") or ""
            else:
                path = d.get("image") or d.get("filepath") or d.get("url") or ""
            if path and not path.startswith("http"):
                path = UPLOADS_URL + "/" + path.lstrip("/")
            title = d.get("title") or kind or "file"
            if not path:
                return ""
            if kind == "link":
                local = path
            else:
                local = urllib.parse.quote(self.asset_cb(path), safe="/:.%-_()")
            if kind == "image":
                s = indent + "![%s](%s)" % (title.replace("]", ""), local)
                if d.get("caption"):
                    s += "\n\n" + indent + "*%s*" % d["caption"]
                return s
            if kind == "link":
                url = d.get("url") or ""
                desc = (d.get("description") or "").strip()
                line = indent + "🔗 [%s](%s)" % (title.replace("]", ""), url)
                if desc:
                    line += "  \n" + indent + "*%s*" % (desc[:300] + ("…" if len(desc) > 300 else ""))
                return line
            if kind == "file":
                return indent + "📎 [%s](%s)" % (title.replace("]", ""), local)
            UNKNOWN_TYPES.add("atomic:" + str(kind))
            return indent + "[%s: %s](%s)" % (kind, title, local)
        if t in ("callout", "callOut", "infoBox", "panel", "alert", "note", "hint"):
            kind = (a.get("type") or a.get("variant") or "NOTE").upper()
            kind = {"INFO": "NOTE", "SUCCESS": "TIP", "DANGER": "CAUTION", "ERROR": "CAUTION"}.get(kind, kind)
            inner = self.blocks(c)
            return "\n".join([indent + "> [!%s]" % kind] +
                             [indent + "> " + ln if ln else indent + ">" for ln in inner.split("\n")])
        if t in ("horizontalRule", "divider"):
            return indent + "---"
        if t in ("image", "imageBlock", "figure"):
            return indent + self.image(n) if (a.get("src") or a.get("url")) else self.blocks(c, indent)
        if t == "table":
            return self.table(n, indent)
        if t in ("details", "toggle", "collapsible"):
            summ, body = "", []
            for x in c:
                if x.get("type") in ("detailsSummary", "summary", "toggleTitle"):
                    summ = self.inline(x.get("content"))
                else:
                    body.append(x)
            return "%s<details>\n<summary>%s</summary>\n\n%s\n\n</details>" % (
                indent, summ, self.blocks(body))
        if t in ("detailsContent",):
            return self.blocks(c, indent)
        if t in ("video", "audio", "youtube", "vimeo", "loom", "iframe") and (a.get("src") or a.get("url")):
            return self.video(a, indent)
        if t in ("embed", "link_preview", "linkPreview",
                 "bookmark", "file", "attachment", "smartEmbed"):
            url = a.get("src") or a.get("url") or a.get("href") or ""
            title = a.get("title") or a.get("name") or a.get("filename") or url
            if t in ("file", "attachment") and url:
                url = self.asset_cb(url)
            return indent + ("[%s: %s](%s)" % (t, title, url) if url else "")
        if t in ("math", "mathBlock", "katex", "blockMath"):
            return indent + "$$\n" + str(a.get("latex") or a.get("value") or self.plain(n)) + "\n$$"
        # unknown block: keep what content it has
        UNKNOWN_TYPES.add(str(t))
        if c and all((x.get("type") == "text" or x.get("type") == "hardBreak") for x in c):
            return self.prefix(self.inline(c), indent)
        return self.blocks(c, indent)

    def video(self, d, indent):
        """Video/audio/embed. Uploaded files are downloaded; hosted players stay links."""
        title = (d.get("title") or d.get("name") or d.get("kind") or "video").replace("]", "")
        fp = d.get("filepath") or d.get("file") or ""
        url = d.get("url") or d.get("src") or d.get("embed_url") or ""
        if fp and not fp.startswith("http"):
            fp = UPLOADS_URL + "/" + fp.lstrip("/")
        target = fp or url
        if not target:
            return ""
        if fp or re.search(r"\.(mp4|mov|m4v|webm|mp3|m4a|wav)(\?|$)", target, re.I):
            local = self.asset_cb(target, video=True)
            return indent + "🎬 [%s](%s)" % (title, urllib.parse.quote(local, safe="/:.%-_()"))
        return indent + "🎬 [%s](%s)" % (title, target)

    def table(self, n, indent):
        rows = []
        for r in n.get("content") or []:
            cells = []
            for cell in r.get("content") or []:
                txt = self.blocks(cell.get("content") or [])
                txt = txt.replace("\n\n", "<br>").replace("\n", "<br>").replace("|", "\\|")
                cells.append(txt.strip())
            rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(indent + ln for ln in out)

    @staticmethod
    def prefix(text, indent):
        if not indent:
            return text
        return "\n".join(indent + ln if ln else "" for ln in text.split("\n"))


# ----------------------------------------------------------------- syncing
class DocSync:
    def __init__(self, host, root_guid, folder, shared=False):
        self.shared = shared  # several links target the same folder -> one subfolder per doc
        self.host = host
        self.root_guid = root_guid
        self.folder_override = folder
        self.api = "https://%s/api/sharefront/doc/" % host
        self.changes = []  # (kind, relpath)
        self.config = load_config()
        self.out = None

    def fetch(self, guid):
        time.sleep(DELAY)
        return json.loads(http_get(self.api + guid))

    def run(self):
        root = self.fetch(self.root_guid)
        title = self.full_title(root, root) or self.root_guid
        if self.folder_override:
            out = self.folder_override
            if not os.path.isabs(out):
                out = os.path.join(OUTPUT_ROOT, out)
            if self.shared:
                out = os.path.join(out, safe_name(title))
        else:
            out = os.path.join(OUTPUT_ROOT, safe_name(title))
        self.out = out
        os.makedirs(out, exist_ok=True)
        self.state_path = os.path.join(out, ".sync-state.json")
        self.migrate_from_old_folder()
        self.state = {}
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                self.state = json.load(f)
        self.state.setdefault("pages", {})
        self.state.setdefault("assets", {})
        self.state.setdefault("asset_files", sorted(set(self.state["assets"].values())))
        self.seen = set()
        self.dirs_to_prune = set()
        self.diffs = {}
        self.state["title"] = title
        self.state["source"] = "https://%s/share/d/%s" % (self.host, self.root_guid)

        log("Syncing '%s' -> %s" % (title, out))
        # root page itself (may have body content)
        # phase 1: fetch every page and decide its local path
        self.jobs = [(root, out, "00 - " + safe_name(title), root, title, True)]
        self.walk(root.get("tree") or {}, out)
        self.linkmap = {}
        for page, d, base, node, _t, is_root in self.jobs:
            if not (is_root and not self.has_content(page.get("body_json") or {})):
                self.linkmap[page.get("guid") or node.get("guid")] = os.path.join(d, base + ".md")
        lm_hash = hashlib.sha256(json.dumps(
            {g: os.path.relpath(v, out) for g, v in self.linkmap.items()}, sort_keys=True).encode()).hexdigest()
        self.force = self.state.get("linkmap_hash") != lm_hash
        # phase 2: render. If it fails partway, keep what was done so the
        # next run doesn't report those pages as new again.
        try:
            for page, d, base, node, t, is_root in self.jobs:
                self.write_page(page, d, base, node, title=t, is_root=is_root)
        except BaseException:
            self.save_state()
            self.write_changelog()
            raise
        self.state["linkmap_hash"] = lm_hash

        # pages that vanished from the tree: report, keep the files
        for guid, info in list(self.state["pages"].items()):
            if guid not in self.seen and not info.get("removed"):
                info["removed"] = datetime.now().isoformat(timespec="seconds")
                self.changes.append(("removed online (local copy kept)", info.get("path")))

        self.state["title"] = title
        self.state["source"] = "https://%s/share/d/%s" % (self.host, self.root_guid)
        self.state["last_sync"] = datetime.now().isoformat(timespec="seconds")
        self.cleanup_unused_assets()
        self.save_state()
        self.write_changelog()
        self.write_index(root)
        for d in sorted(self.dirs_to_prune, key=len, reverse=True):
            self.prune_empty(d, self.out)
        set_registry(self.root_guid, self.out)
        return title, self.changes

    def save_state(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    # -- moving a doc when its folder changes ---------------------------
    def owns(self, folder):
        sp = os.path.join(folder, ".sync-state.json")
        if not os.path.exists(sp):
            return None
        try:
            with open(sp, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            return None
        return st if (st.get("source") or "").rstrip("/").endswith("/" + self.root_guid) else None

    def migrate_from_old_folder(self):
        """If this doc was last synced somewhere else, move its files here."""
        candidates = [get_registry(self.root_guid)]
        if self.shared:  # doc used to live directly in the shared folder
            candidates.append(os.path.dirname(self.out))
        for old in candidates:
            if not old or os.path.normpath(old) == os.path.normpath(self.out):
                continue
            st = self.owns(old)
            if not st:
                continue
            if os.path.exists(self.state_path):
                log("WARN: '%s' also has an older copy in %s (not moved, both exist)" % (self.out, old))
                return
            items = [v.get("path") for v in st.get("pages", {}).values() if v.get("path")]
            items += list(st.get("asset_files") or []) + list((st.get("assets") or {}).values())
            items += [".raw", "README.md", "CHANGES.md", ".sync-state.json",
                      safe_name(st.get("title") or "") + ".pdf", st.get("pdf_name") or ""]
            moved, dirs = 0, set()
            for rel in sorted(set(items)):
                src, dst = os.path.join(old, rel), os.path.join(self.out, rel)
                if not os.path.exists(src) or os.path.exists(dst):
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                moved += 1
                dirs.add(os.path.dirname(src))
            for d in sorted(dirs, key=len, reverse=True):
                self.prune_empty(d, old, include_stop=not self.out.startswith(old.rstrip("/") + "/"))
            log("Moved %d item(s) of this doc from %s" % (moved, old))
            self.changes.append(("moved folder", "%s -> %s" % (old, self.out)))
            return

    # -- assets no page uses any more -----------------------------------
    def cleanup_unused_assets(self):
        pages = self.state["pages"].values()
        if any(v.get("path") and "assets" not in v for v in pages):
            return  # not every page has been rendered by this version yet
        used = set()
        for v in pages:
            used.update(v.get("assets") or [])
        keep = []
        for rel in self.state.get("asset_files") or []:
            if rel in used:
                keep.append(rel)
                continue
            p = os.path.join(self.out, rel)
            if os.path.isfile(p):
                os.remove(p)
            self.dirs_to_prune.add(os.path.dirname(p))
        self.state["asset_files"] = sorted(set(keep))
        self.state["assets"] = {u: r for u, r in self.state["assets"].items() if r in used}

    def walk(self, node, dirpath):
        children = sorted(node.get("children") or [], key=lambda c: c.get("sortorder") or 0)
        for i, child in enumerate(children, 1):
            guid = child["guid"]
            page = self.fetch(guid)
            full_title = self.full_title(page, child)
            base = "%02d - %s" % (i, safe_name(full_title))
            if child.get("children"):
                sub = os.path.join(dirpath, base)
                os.makedirs(sub, exist_ok=True)
                self.jobs.append((page, sub, "00 - " + safe_name(full_title), child, full_title, False))
                self.walk(child, sub)
            else:
                self.jobs.append((page, dirpath, base, child, full_title, False))

    @staticmethod
    def full_title(page, child):
        """Tree titles are truncated with '...'; prefer the page's first H1."""
        t = page.get("title") or child.get("title") or ""
        body = (page.get("body_json") or {}).get("content") or []
        for n in body[:3]:
            if n.get("type") == "heading":
                h = "".join(x.get("text", "") for x in n.get("content") or [] if x.get("type") == "text").strip()
                if h and (t.endswith("...") or t.endswith("…") or not t):
                    return h
                break
        return t

    def write_page(self, page, dirpath, base, node, title=None, is_root=False):
        guid = page.get("guid") or node.get("guid")
        self.seen.add(guid)
        body = page.get("body_json") or {"type": "doc", "content": []}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        md_path = os.path.join(dirpath, base + ".md")
        rel = os.path.relpath(md_path, self.out)
        prev = self.state["pages"].get(guid)

        # skip near-empty root page
        if is_root and not self.has_content(body):
            self.state["pages"][guid] = {"hash": h, "path": None, "title": page.get("title")}
            return

        same_content = bool(prev) and prev.get("hash") == h
        if same_content and prev.get("path") == rel and os.path.exists(md_path) \
                and prev.get("cv") == CONVERTER_VERSION and not self.force \
                and all(os.path.exists(os.path.join(self.out, a)) for a in prev.get("assets") or []):
            prev.pop("removed", None)
            return
        rerender_only = same_content and prev.get("path") == rel

        assets_dir = os.path.join(dirpath, "assets")
        page_assets = []

        def asset_cb(url, video=False):
            if video and not self.config.get("videos", True):
                return url
            return self.get_asset(url, assets_dir, dirpath, page_assets)

        def link_cb(href):
            m = re.match(r"^(?:https?://[^/]+)?/(?:a/docs|share/d)/([A-Za-z0-9]+)(#.*)?$", href)
            if m and m.group(1) in self.linkmap:
                return os.path.relpath(self.linkmap[m.group(1)], dirpath) + (m.group(2) or "")
            if href.startswith("/"):
                return "https://%s%s" % (self.host, href)
            return href

        old_text = None
        if prev and prev.get("path") and not rerender_only:
            op = os.path.join(self.out, prev["path"])
            if os.path.isfile(op):
                with open(op, encoding="utf-8") as f:
                    old_text = f.read()

        conv = Converter(asset_cb, link_cb)
        md = conv.blocks(body.get("content") or [])
        title = title or page.get("title") or guid
        if not md.lstrip().startswith("# "):
            md = "# %s\n\n%s" % (title, md)
        src = "https://%s/share/d/%s" % (self.host, guid)
        header = "<!-- source: %s | synced: %s -->\n\n" % (src, datetime.now().strftime("%Y-%m-%d %H:%M"))
        new_text = header + md.rstrip() + "\n"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        if old_text is not None:
            self.diffs[rel] = text_diff(old_text, new_text)

        # raw JSON kept for fidelity / re-conversion later
        raw_dir = os.path.join(self.out, ".raw")
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, guid + ".json"), "w", encoding="utf-8") as f:
            json.dump(page, f, ensure_ascii=False)

        if prev and prev.get("path") and prev.get("path") != rel:
            old = os.path.join(self.out, prev["path"])
            if os.path.exists(old):
                os.remove(old)  # our own generated file at its old name
            self.dirs_to_prune.add(os.path.dirname(old))
            self.changes.append(("renamed/moved", "%s -> %s" % (prev["path"], rel)))
        elif rerender_only:
            pass  # only the converter changed; not a content update
        elif prev:
            self.changes.append(("updated", rel))
        else:
            self.changes.append(("new", rel))
        self.state["pages"][guid] = {"hash": h, "path": rel, "title": title, "cv": CONVERTER_VERSION,
                                     "assets": sorted(set(page_assets))}

    @staticmethod
    def has_content(body):
        for n in body.get("content") or []:
            if n.get("content") or n.get("type") not in ("paragraph",):
                return True
        return False

    def get_asset(self, url, assets_dir, md_dir, page_assets):
        if url.startswith("/"):
            url = "https://%s%s" % (self.host, url)
        if not url.startswith("http"):
            return url
        known = self.state["assets"].get(url)
        known = os.path.join(self.out, known) if known else None
        path_part = urllib.parse.urlparse(url).path
        ext = os.path.splitext(path_part)[1][:6] or ".bin"
        stem = safe_name(os.path.splitext(os.path.basename(path_part))[0], 50)
        name = "%s-%s%s" % (stem, hashlib.sha1(url.encode()).hexdigest()[:8], ext)
        os.makedirs(assets_dir, exist_ok=True)
        dest = os.path.join(assets_dir, name)
        try:
            if not os.path.exists(dest):
                if known and os.path.isfile(known):
                    shutil.copy2(known, dest)  # page moved: bring its asset along
                else:
                    download_to(url, dest)
            rel = os.path.relpath(dest, self.out)
            self.state["assets"][url] = rel
            if rel not in self.state["asset_files"]:
                self.state["asset_files"].append(rel)
            page_assets.append(rel)
            return os.path.relpath(dest, md_dir)
        except Exception as e:
            log("WARN: asset download failed (%s): %s" % (e, url))
            return url

    @staticmethod
    def prune_empty(d, stop, include_stop=False):
        """Remove d and its empty parents, up to (not including) stop."""
        stop = os.path.normpath(stop)
        d = os.path.normpath(d)
        while d.startswith(stop) and (d != stop or include_stop) and os.path.isdir(d):
            left = [x for x in os.listdir(d) if x != ".DS_Store"]
            if left:
                return
            ds = os.path.join(d, ".DS_Store")
            if os.path.exists(ds):
                os.remove(ds)
            os.rmdir(d)
            if d == stop:
                return
            d = os.path.dirname(d)

    def write_changelog(self):
        if not self.changes:
            return
        p = os.path.join(self.out, "CHANGES.md")
        old = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                old = f.read()
            old = re.sub(r"^# Changes\n+", "", old)
        lines = ["## %s" % datetime.now().strftime("%Y-%m-%d %H:%M")]
        for k, v in self.changes:
            lines.append("- **%s**: %s" % (k, v))
            lines += diff_block(self.diffs.get(v), "  ")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Changes\n\n" + "\n".join(lines) + "\n\n" + old)

    def write_index(self, root):
        """README.md: clickable table of contents of the local copy."""
        lines = ["# %s" % (self.state.get("title") or root.get("title") or ""), "",
                 "Source: https://%s/share/d/%s  " % (self.host, self.root_guid),
                 "Last sync: %s" % self.state["last_sync"], ""]
        pages = [(v["path"], v.get("title")) for v in self.state["pages"].values()
                 if v.get("path") and not v.get("removed")]
        for path, t in sorted(pages):
            depth = path.count(os.sep) - (1 if os.path.basename(path).startswith("00 - ") else 0)
            lines.append("%s- [%s](%s)" % ("  " * max(depth, 0), t or path,
                                           urllib.parse.quote(path)))
        with open(os.path.join(self.out, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def text_diff(old, new, max_lines=80):
    """Readable unified diff of a page's text, without the 'synced:' header line."""
    strip = lambda t: [ln for ln in t.splitlines() if not ln.startswith("<!-- source:")]
    d = list(difflib.unified_diff(strip(old), strip(new), lineterm="", n=1))[2:]
    d = [ln for ln in d if not ln.startswith("@@")] if len(d) < 6 else d
    if not d:
        return None
    if len(d) > max_lines:
        d = d[:max_lines] + ["… (%d more lines)" % (len(d) - max_lines)]
    return d


def diff_block(diff, indent=""):
    if not diff:
        return []
    out = [indent + "<details><summary>show what changed</summary>", "", indent + "```diff"]
    out += [indent + ln.replace("```", "ˋˋˋ") for ln in diff]
    out += [indent + "```", "", indent + "</details>"]
    return out


def write_whats_new(results, keep=60):
    """One file across all courses, newest sync first."""
    if not any(r["changes"] for r in results):
        return
    lines = ["## %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    for r in results:
        if not r["changes"]:
            continue
        folder = os.path.relpath(r["out"], OUTPUT_ROOT)
        lines.append("### %s" % r["title"])
        lines.append("*%s*" % folder)
        lines.append("")
        for k, v in r["changes"]:
            p = os.path.join(r["out"], v) if v and v.endswith(".md") else None
            if p and os.path.exists(p):
                rel = os.path.relpath(p, OUTPUT_ROOT)
                lines.append("- **%s**: [%s](<%s>)" % (k, os.path.basename(v)[:-3], rel))
            else:
                lines.append("- **%s**: %s" % (k, v))
            lines += diff_block(r["diffs"].get(v), "  ")
        lines.append("")
    old = ""
    if os.path.exists(WHATS_NEW_FILE):
        with open(WHATS_NEW_FILE, encoding="utf-8") as f:
            old = f.read()
        m = re.search(r"(?m)^## \d{4}-", old)
        old = old[m.start():] if m else ""
    sections = [s for s in re.split(r"(?m)^(?=## \d{4}-)", old) if s.strip()][: keep - 1]
    head = "# What's new in your course docs\n\nNewest first. Updated pages include what changed.\n\n"
    with open(WHATS_NEW_FILE, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n" + "".join(sections))


def _load_registry():
    """doc id -> absolute folder. Stored relative to OUTPUT_ROOT so it works on the Mac and in Docker."""
    try:
        with open(REGISTRY_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    return {g: (v if os.path.isabs(v) else os.path.normpath(os.path.join(OUTPUT_ROOT, v))) for g, v in raw.items()}


def get_registry(guid):
    return _load_registry().get(guid)


def set_registry(guid, folder):
    reg = _load_registry()
    reg[guid] = folder
    rel = {}
    for g, v in reg.items():
        r = os.path.relpath(v, OUTPUT_ROOT)
        rel[g] = v if r.startswith("..") else r
    with open(REGISTRY_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rel, f, indent=1, ensure_ascii=False)
    os.replace(REGISTRY_FILE + ".tmp", REGISTRY_FILE)


def main(argv):
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Another sync is already running; skipping this one.")
        return 0
    all_links = read_links()
    if argv:
        want = [parse_link(a) for a in argv]
        want = [l for l in want if l]
        # use the folder from links.txt when a link is listed there
        listed = {g: (h, g, f) for h, g, f in all_links}
        links = [listed.get(g, (h, g, f)) for h, g, f in want]
    else:
        links = all_links
    if not links:
        print("No links. Add share URLs to %s" % LINKS_FILE)
        return 1
    summary, failed, results = [], 0, []
    def norm(f):
        return os.path.normpath(f if os.path.isabs(f) else os.path.join(OUTPUT_ROOT, f)).lower() if f else None
    targets = [norm(f) for _, _, f in {(h, g, f) for h, g, f in all_links + links}]
    for host, guid, folder in links:
        shared = folder is not None and targets.count(norm(folder)) > 1
        try:
            ds = DocSync(host, guid, folder, shared)
            title, changes = ds.run()
            results.append({"guid": guid, "title": title, "out": ds.out, "changes": changes, "diffs": ds.diffs})
            n = len(changes)
            log("Done '%s': %d change(s)" % (title, n))
            for k, v in changes:
                log("   %s: %s" % (k, v))
            if n:
                summary.append("%s: %d" % (title, n))
        except Exception as e:
            failed += 1
            log("ERROR syncing %s/%s: %r" % (host, guid, e))
    if UNKNOWN_TYPES:
        log("Note: unhandled node types (content kept as text): %s" % ", ".join(sorted(UNKNOWN_TYPES)))
    try:
        write_whats_new(results)
    except Exception as e:
        log("WARN: could not update WHATS-NEW.md: %r" % e)
    after_sync(results)
    if summary:
        notify("Course docs updated", "; ".join(summary)[:200])
    if failed:
        notify("Course docs sync", "%d link(s) failed - see sync.log" % failed)
    return 1 if failed else 0


def after_sync(results):
    """Site, PDFs and git history. Each step is optional (config.json) and never fails the sync."""
    if CODE_DIR not in sys.path:
        sys.path.insert(0, CODE_DIR)
    cfg = load_config()
    if cfg.get("site", True):
        try:
            import docsite
            docsite.build_site()
        except Exception as e:
            log("WARN: site build failed: %r" % e)
    if cfg.get("pdf", True):
        try:
            import docsite
            for r in results:
                docsite.build_pdf(r["guid"], force=bool(r["changes"]))
        except Exception as e:
            log("WARN: PDF export failed: %r" % e)
    if cfg.get("git", True):
        try:
            import history
            msg = "; ".join("%s: %d change(s)" % (r["title"], len(r["changes"])) for r in results if r["changes"])
            history.commit("Sync %s%s" % (datetime.now().strftime("%Y-%m-%d %H:%M"), (" - " + msg) if msg else ""))
        except Exception as e:
            log("WARN: git history failed: %r" % e)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
