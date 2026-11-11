"""
Git history of the synced doc folders.

The repository lives in data/history.git with ~/Courses as its work tree, but an
exclude file limits it to the doc folders only, so nothing else in ~/Courses (your videos,
your own projects and their own git repos) is ever tracked or touched.
"""
import os
import subprocess

import lectern_sync as core

GIT_DIR = os.path.join(core.HERE, "history.git")
IGNORED = [".raw/", ".sync-state.json", ".sync-state.json.tmp", "*.part", "*.tmp", "*.pdf",
           ".DS_Store", "*.mp4", "*.mov", "*.m4v", "*.webm"]


def git(*args, check=True, capture=True):
    cmd = ["git", "--git-dir=" + GIT_DIR, "--work-tree=" + core.OUTPUT_ROOT] + list(args)
    r = subprocess.run(cmd, cwd=core.OUTPUT_ROOT, text=True,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args[:2]), (r.stderr or "").strip()[:300]))
    return r.stdout if capture else ""


def _esc(part):
    """Escape a literal path component for a gitignore pattern."""
    out = ""
    for ch in part:
        if ch in "*?[]\\!#":
            out += "\\" + ch
        else:
            out += ch
    if out.endswith(" "):
        out = out[:-1] + "\\ "
    return out


def _write_excludes(folders):
    """Ignore everything in the courses folder except the doc folders."""
    rules = ["/*"]
    allowed = set()
    for f in sorted(folders):
        rel = os.path.relpath(f, core.OUTPUT_ROOT)
        if rel.startswith(".."):
            continue  # folder outside ~/Courses: not tracked
        parts = rel.split(os.sep)
        for i in range(len(parts)):
            prefix = "/" + "/".join(_esc(p) for p in parts[: i + 1])
            if prefix in allowed:
                continue
            allowed.add(prefix)
            rules.append("!%s/" % prefix)
            if i < len(parts) - 1:
                rules.append("%s/*" % prefix)
    rules += IGNORED
    os.makedirs(os.path.join(GIT_DIR, "info"), exist_ok=True)
    with open(os.path.join(GIT_DIR, "info", "exclude"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rules) + "\n")


def ensure_repo():
    if not os.path.isdir(GIT_DIR):
        subprocess.run(["git", "init", "--bare", "-q", GIT_DIR], check=True)
        git("config", "core.bare", "false")
        git("config", "user.name", "lectern")
        git("config", "user.email", "lectern@localhost")
        git("config", "core.quotepath", "false")
        git("config", "core.precomposeunicode", "true")
        git("config", "commit.gpgsign", "false")


def commit(message):
    ensure_repo()
    folders = set(core._load_registry().values())
    _write_excludes(folders)
    git("add", "-A", "--", ".")
    if not git("status", "--porcelain"):
        return None
    git("commit", "-q", "-m", message)
    sha = git("rev-parse", "--short", "HEAD").strip()
    core.log("History: saved snapshot %s" % sha)
    return sha


def log_lines(n=20, path=None):
    if not os.path.isdir(GIT_DIR):
        return ""
    args = ["log", "-n", str(n), "--date=format:%Y-%m-%d %H:%M", "--pretty=format:%h  %ad  %s"]
    if path:
        args += ["--", path]
    return git(*args, check=False)


def show(sha):
    return git("show", "--stat", "--pretty=format:%h  %ad%n%s%n", "--date=format:%Y-%m-%d %H:%M", sha)


def diff(sha, path=None):
    args = ["show", "--pretty=format:", "--color=always", sha]
    if path:
        args += ["--", path]
    return git(*args)


def restore(path, sha):
    """Put back a file or folder as it was in snapshot <sha>."""
    p = path if os.path.isabs(path) else os.path.join(core.OUTPUT_ROOT, path)
    rel = os.path.relpath(p, core.OUTPUT_ROOT)
    git("checkout", sha, "--", rel)
    return rel
