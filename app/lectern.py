#!/usr/bin/env python3
"""lectern - Lectern keeps local copies of bit.ai / bitdocs.ai course docs.

  lectern sync [LINK...]            sync everything (or just these links)
  lectern add LINK [FOLDER]         add a link and download it now
  lectern move LINK FOLDER          change where a doc is saved (files are moved)
  lectern remove LINK               stop syncing a link (local files are kept)
  lectern list                      show your links, folders and last sync
  lectern scan URL|FILE|-|--clipboard [--add] [--folder FOLDER]
                                    find every doc link on a page / saved file / clipboard
  lectern search WORDS...           search all downloaded docs
  lectern open                      open the local docs site in your browser
  lectern site                      rebuild the local docs site
  lectern pdf [DOC|all]             (re)build PDFs
  lectern news                      open WHATS-NEW.md
  lectern history [N]               list saved snapshots
  lectern history show SHA          what changed in a snapshot
  lectern history restore PATH SHA  put a file/folder back as it was in a snapshot
  lectern schedule                  show the automatic schedule
  lectern schedule daily 09:00 [21:00 ...]
  lectern schedule weekdays 09:00 [...]
  lectern schedule every 6h
  lectern schedule off | on
  lectern config [KEY on|off]       site / pdf / git / videos (all on by default)

Docker (run on the Mac):
  lectern start | stop | restart    start/stop the background container (web UI + scheduler)
  lectern ui                        open the web UI (http://localhost:8790)
  lectern status | logs             is it running? / follow its log
  lectern rebuild                   rebuild the image after code changes

FOLDER is relative to ~/Courses unless it starts with /.
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import lectern_sync as core  # noqa: E402

TTY = sys.stdout.isatty()


def c(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if TTY else text


def die(msg):
    print(c("error: ", "31") + msg, file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------ links.txt
def read_lines():
    if not os.path.exists(core.LINKS_FILE):
        return []
    with open(core.LINKS_FILE, encoding="utf-8") as f:
        return f.read().splitlines()


def write_lines(lines):
    tmp = core.LINKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, core.LINKS_FILE)


def find_line(lines, guid):
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            continue
        p = core.parse_link(ln)
        if p and p[1] == guid:
            return i, p
    return None, None


def need_link(text):
    p = core.parse_link(text)
    if not p:
        die("not a bitdocs share link: %s" % text)
    return p


def fetch_title(host, guid):
    try:
        d = json.loads(core.http_get("https://%s/api/sharefront/doc/%s" % (host, guid)))
        return d.get("title") or guid
    except Exception as e:
        return None if "404" in str(e) else "(could not check: %s)" % e


def cmd_add(args):
    if not args:
        die("usage: lectern add LINK [FOLDER]")
    host, guid, _ = need_link(args[0])
    folder = " ".join(args[1:]).strip() or None
    lines = read_lines()
    i, existing = find_line(lines, guid)
    if existing:
        print("Already in links.txt%s." % (" -> " + existing[2] if existing[2] else ""))
        print("Use `lectern move %s FOLDER` to change its folder." % args[0])
        return
    title = fetch_title(host, guid)
    if title is None:
        die("that link does not exist or is not shared publicly")
    url = "https://%s/share/d/%s" % (host, guid)
    lines.append(url + (" -> " + folder if folder else ""))
    write_lines(lines)
    print("Added %s %s" % (c(title, "1"), ("-> " + folder) if folder else "(folder: ~/Courses/%s)" % core.safe_name(title)))
    core.main([url])


def cmd_move(args):
    if len(args) < 2:
        die("usage: lectern move LINK FOLDER")
    host, guid, _ = need_link(args[0])
    folder = " ".join(args[1:]).strip()
    lines = read_lines()
    i, existing = find_line(lines, guid)
    if existing is None:
        die("that link is not in links.txt (use `lectern add`)")
    lines[i] = "https://%s/share/d/%s -> %s" % (host, guid, folder)
    write_lines(lines)
    print("Folder changed; moving files...")
    core.main(["https://%s/share/d/%s" % (host, guid)])


def cmd_remove(args):
    if not args:
        die("usage: lectern remove LINK")
    _, guid, _ = need_link(args[0])
    lines = read_lines()
    i, existing = find_line(lines, guid)
    if existing is None:
        die("that link is not in links.txt")
    removed = lines.pop(i)
    write_lines(lines)
    out = core._load_registry().get(guid)
    print("Removed: %s" % removed.strip())
    if out:
        print("Local files kept in: %s" % out)


def cmd_list(_args):
    reg = core._load_registry()
    links = core.read_links()
    if not links:
        print("No links yet. Add one with: lectern add LINK [FOLDER]")
        return
    for host, guid, folder in links:
        out = reg.get(guid)
        st = {}
        if out and os.path.exists(os.path.join(out, ".sync-state.json")):
            with open(os.path.join(out, ".sync-state.json"), encoding="utf-8") as f:
                st = json.load(f)
        pages = sum(1 for v in st.get("pages", {}).values() if v.get("path") and not v.get("removed"))
        title = st.get("title") or "(not synced yet)"
        when = (st.get("last_sync") or "").replace("T", " ")[:16]
        print(c(title, "1"))
        print("   https://%s/share/d/%s" % (host, guid))
        print("   %s" % (os.path.relpath(out, core.OUTPUT_ROOT) if out else (folder or "-")))
        if st:
            print(c("   %d pages · last sync %s" % (pages, when), "2"))


# ----------------------------------------------------------------- scan
LINK_RE = re.compile(r"https?://[A-Za-z0-9.-]+\.bitdocs\.ai/share/d/[A-Za-z0-9]+")


def cmd_scan(args):
    add = "--add" in args
    folder = None
    if "--folder" in args:
        k = args.index("--folder")
        folder = " ".join(args[k + 1:]).strip() or None
        args = args[:k]
    args = [a for a in args if a != "--add"]
    if not args:
        die("usage: lectern scan URL|FILE|-|--clipboard [--add] [--folder FOLDER]")
    src = args[0]
    if src == "--clipboard":
        if not shutil.which("pbpaste"):
            die("--clipboard works from the Mac command (lectern), not inside the container")
        text = subprocess.run(["pbpaste"], stdout=subprocess.PIPE, text=True).stdout
    elif src == "-":
        text = sys.stdin.read()
    elif os.path.exists(os.path.expanduser(src)):
        with open(os.path.expanduser(src), encoding="utf-8", errors="replace") as f:
            text = f.read()
    elif src.startswith("http"):
        try:
            text = core.http_get(src)
        except Exception as e:
            die("could not open %s (%s). If the page needs a login, save it in your browser "
                "(File > Save Page As) and scan the saved file, or copy the page and use --clipboard." % (src, e))
    else:
        die("not a file or URL: %s" % src)
    found, seen = [], set()
    for m in LINK_RE.findall(text.replace("&amp;", "&")):
        p = core.parse_link(m)
        if p and p[1] not in seen:
            seen.add(p[1])
            found.append(p)
    if not found:
        print("No bitdocs share links found.")
        if src.startswith("http"):
            print("If the course page needs a login, the links are not visible to this tool. Save the page "
                  "in your browser and run: lectern scan ~/Downloads/<saved page>.html")
        return
    known = {g for _, g, _ in core.read_links()}
    new = []
    print("Found %d doc link(s):" % len(found))
    for host, guid, _ in found:
        title = fetch_title(host, guid)
        url = "https://%s/share/d/%s" % (host, guid)
        tag = c("already added", "2") if guid in known else (c("not available", "31") if title is None else c("new", "32"))
        print("  [%s] %s\n        %s" % (tag, title or "?", url))
        if guid not in known and title is not None:
            new.append(url)
    if not new:
        return
    if not add:
        print("\nAdd them all with:  lectern scan %s --add%s" % (src if " " not in src else '"%s"' % src,
                                                               " --folder FOLDER" if not folder else ""))
        return
    lines = read_lines()
    for url in new:
        lines.append(url + (" -> " + folder if folder else ""))
    write_lines(lines)
    print("\nAdded %d link(s). Downloading..." % len(new))
    core.main(new)


# --------------------------------------------------------------- search
def cmd_search(args):
    if not args:
        die("usage: lectern search WORDS...")
    terms = [t.lower() for t in args]
    reg = core._load_registry()
    folders = [reg[g] for _, g, _ in core.read_links() if g in reg]
    hits = 0
    for folder in folders:
        for dirpath, dirnames, files in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "assets")
            for fn in sorted(files):
                if not fn.endswith(".md") or fn in ("README.md", "CHANGES.md"):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                low = "\n".join(lines).lower()
                if not all(t in low for t in terms):
                    continue
                hits += 1
                print(c(os.path.relpath(p, core.OUTPUT_ROOT), "1;34"))
                shown = 0
                for n, ln in enumerate(lines, 1):
                    l = ln.lower()
                    if any(t in l for t in terms) and not ln.startswith("<!--"):
                        snippet = ln.strip()
                        if len(snippet) > 160:
                            k = min(l.find(t) for t in terms if t in l)
                            snippet = ("…" if k > 60 else "") + ln.strip()[max(0, k - 60):k + 100] + "…"
                        for t in terms:
                            snippet = re.sub("(?i)(" + re.escape(t) + ")", c(r"\1", "1;33"), snippet)
                        print("  %s %s" % (c("%4d:" % n, "2"), snippet))
                        shown += 1
                        if shown >= 4:
                            break
    print(c("\n%d page(s) match." % hits, "2"))


# ---------------------------------------------------- site / pdf / news
def cmd_site(_args, open_it=False):
    import docsite
    path = docsite.build_site()
    print("Site: %s" % path)
    if open_it and shutil.which("open"):
        subprocess.run(["open", path])


def cmd_pdf(args):
    import docsite
    docs = docsite.list_docs()
    want = " ".join(args).strip().lower()
    chosen = [d for d in docs if not want or want == "all" or want in (d["state"].get("title") or "").lower()
              or want in d["guid"].lower()]
    if not chosen:
        die("no doc matches '%s'" % want)
    for d in chosen:
        print("Building PDF: %s ..." % d["state"].get("title"))
        p = docsite.build_pdf(d["guid"], force=True)
        print("   " + (p or c("failed (see sync.log)", "31")))


def cmd_news(_args):
    if not os.path.exists(core.WHATS_NEW_FILE):
        print("Nothing yet: WHATS-NEW.md is written after a sync finds changes.")
        return
    if shutil.which("open"):
        subprocess.run(["open", core.WHATS_NEW_FILE])
    else:
        print(core.WHATS_NEW_FILE)


# -------------------------------------------------------------- history
def cmd_history(args):
    import history
    if args and args[0] == "show":
        if len(args) < 2:
            die("usage: lectern history show SHA")
        print(history.show(args[1]))
        print(history.diff(args[1]))
        return
    if args and args[0] == "restore":
        if len(args) < 3:
            die("usage: lectern history restore PATH SHA")
        rel = history.restore(args[1], args[2])
        print("Restored %s as it was in %s." % (rel, args[2]))
        print("Note: if that page changes online, the next sync will update it again.")
        return
    n = int(args[0]) if args and args[0].isdigit() else 20
    out = history.log_lines(n)
    print(out or "No snapshots yet (one is saved after each sync).")


# ------------------------------------------------------------- schedule
# The schedule lives in config.json and is run by `lectern daemon` (the Docker container).
DEFAULT_SCHEDULE = {"mode": "daily", "times": ["09:00"], "enabled": True}
LAST_RUN_FILE = os.path.join(core.HERE, ".last-scheduled-run")


def get_schedule():
    sch = dict(DEFAULT_SCHEDULE)
    sch.update(core.load_config().get("schedule") or {})
    return sch


def set_schedule(sch):
    cfg = core.load_config()
    cfg["schedule"] = sch
    core.save_config(cfg)


def describe_schedule(sch=None):
    sch = sch or get_schedule()
    state = "on" if sch.get("enabled", True) else "off"
    if sch["mode"] == "every":
        return "Every %g hour(s) (%s)" % (sch["hours"], state)
    when = "Weekdays (Mon-Fri)" if sch["mode"] == "weekdays" else "Every day"
    return "%s at %s (%s, %s time)" % (when, ", ".join(sch["times"]), state, os.environ.get("TZ") or "local")


def parse_time(t):
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t.strip().lower())
    if not m:
        die("bad time '%s' (use 09:00 or 9pm)" % t)
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mnt < 60):
        die("bad time '%s'" % t)
    return "%02d:%02d" % (h, mnt)


def cmd_schedule(args):
    sch = get_schedule()
    if not args:
        print(describe_schedule(sch))
        return
    mode = args[0]
    if mode in ("off", "on"):
        sch["enabled"] = mode == "on"
    elif mode == "every":
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*h?", " ".join(args[1:]).strip().lower())
        if not m or float(m.group(1)) < 1:
            die("usage: lectern schedule every 6h  (1 hour minimum)")
        sch = {"mode": "every", "hours": float(m.group(1)), "enabled": True}
    elif mode in ("daily", "weekdays"):
        times = sorted({parse_time(t) for t in args[1:]}) or ["09:00"]
        sch = {"mode": mode, "times": times, "enabled": True}
    else:
        die("usage: lectern schedule [daily|weekdays TIME...|every 6h|off|on]")
    set_schedule(sch)
    print(describe_schedule(sch))
    print(c("The running container picks this up within a minute.", "2"))


def last_due(sch, now):
    """Most recent scheduled moment at or before now (daily / weekdays)."""
    from datetime import datetime as dt, timedelta
    best = None
    for back in range(0, 8):
        day = (now - timedelta(days=back)).date()
        if sch["mode"] == "weekdays" and day.weekday() >= 5:
            continue
        for t in sch["times"]:
            h, m = map(int, t.split(":"))
            moment = dt(day.year, day.month, day.day, h, m)
            if moment <= now and (best is None or moment > best):
                best = moment
        if best:
            return best
    return best


def read_last_run():
    from datetime import datetime as dt
    try:
        with open(LAST_RUN_FILE) as f:
            return dt.fromisoformat(f.read().strip())
    except Exception:
        return None


def write_last_run(when):
    with open(LAST_RUN_FILE, "w") as f:
        f.write(when.isoformat(timespec="seconds"))


def next_due(sch, now=None):
    """When the next scheduled sync will happen (None if off)."""
    from datetime import datetime as dt, timedelta
    now = now or dt.now()
    if not sch.get("enabled", True):
        return None
    if sch["mode"] == "every":
        last = read_last_run() or now
        return max(last + timedelta(hours=float(sch["hours"])), now)
    for ahead in range(0, 8):
        day = (now + timedelta(days=ahead)).date()
        if sch["mode"] == "weekdays" and day.weekday() >= 5:
            continue
        for t in sorted(sch["times"]):
            h, m = map(int, t.split(":"))
            moment = dt(day.year, day.month, day.day, h, m)
            if moment > now:
                return moment
    return None


def scheduler_loop(stop=None):
    """Sync on schedule. Missed runs (Mac asleep, Docker stopped) run as soon as we are up."""
    import time as _time
    from datetime import datetime as dt, timedelta
    core.log("Scheduler started: %s" % describe_schedule())
    if read_last_run() is None:
        write_last_run(dt.now())
    shown = None
    while not (stop and stop.is_set()):
        sch = get_schedule()
        desc = describe_schedule(sch)
        if desc != shown:
            if shown is not None:
                core.log("Schedule changed: %s" % desc)
            shown = desc
        now = dt.now()
        last = read_last_run() or now
        due = None
        if sch.get("enabled", True):
            if sch["mode"] == "every":
                nxt = last + timedelta(hours=float(sch["hours"]))
                due = nxt if nxt <= now else None
            else:
                d = last_due(sch, now)
                due = d if d and d > last else None
        if due:
            core.log("Scheduled sync (due %s)" % due.strftime("%Y-%m-%d %H:%M"))
            write_last_run(now)
            subprocess.run([sys.executable, "-I", os.path.join(core.CODE_DIR, "lectern_sync.py")])
        if stop:
            stop.wait(30)
        else:
            _time.sleep(30)


def cmd_daemon(_args):
    scheduler_loop()


def cmd_serve(args):
    """Web UI + scheduler (what the Docker container runs)."""
    import threading
    import webui
    port = int(os.environ.get("LECTERN_PORT") or (args[0] if args else 8790))
    threading.Thread(target=scheduler_loop, daemon=True).start()
    webui.serve("0.0.0.0" if os.environ.get("LECTERN_IN_DOCKER") else "127.0.0.1", port)


# --------------------------------------------------------------- config
def cmd_config(args):
    cfg = core.load_config()
    if not args:
        for k, v in cfg.items():
            print("%-7s %s" % (k, "on" if v else "off"))
        return
    if len(args) != 2 or args[0] not in core.DEFAULT_CONFIG or args[1] not in ("on", "off"):
        die("usage: lectern config [site|pdf|git|videos on|off]")
    cfg[args[0]] = args[1] == "on"
    core.save_config(cfg)
    print("%s is now %s" % (args[0], args[1]))


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]
    table = {
        "sync": lambda a: sys.exit(core.main(a)),
        "add": cmd_add, "move": cmd_move, "remove": cmd_remove, "rm": cmd_remove, "list": cmd_list, "ls": cmd_list,
        "scan": cmd_scan, "search": cmd_search, "find": cmd_search,
        "site": cmd_site, "open": lambda a: cmd_site(a, open_it=True), "pdf": cmd_pdf, "news": cmd_news,
        "history": cmd_history, "schedule": cmd_schedule, "config": cmd_config, "daemon": cmd_daemon, "serve": cmd_serve,
    }
    if cmd not in table:
        die("unknown command '%s' (see: lectern help)" % cmd)
    table[cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
