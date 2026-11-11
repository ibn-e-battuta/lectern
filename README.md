# Lectern

Lectern keeps local copies of course documents published on **bit.ai / bitdocs.ai** and keeps them up to date. Give it the share links of the docs that come with your courses. It downloads every page, image and attachment into the matching course folder, checks for changes on a schedule, and tells you exactly what changed.

It runs as a single Docker container with a web UI, and works on its own or next to [Lyceum](https://github.com/ibn-e-battuta/lyceum), the self-hosted video course player.

- [How it works](#how-it-works)
- [Features](#features)
- [Quick start](#quick-start)
- [Using Lectern](#using-lectern)
- [What Lectern writes, and where](#what-lectern-writes-and-where)
- [Configuration](#configuration)
- [Running Lectern with Lyceum](#running-lectern-with-lyceum)
- [Running without Docker](#running-without-docker)
- [Security and privacy](#security-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

## How it works

```
 bitdocs.ai share links ──▶  Lectern  ──▶  your courses folder
 (one per document)          │              Course A/Docs/  ← Markdown, images, attachments, PDF
                             │              Course B/Docs/
                             └──▶  Lectern's data folder
                                   links, settings, history, What's new, docs site
```

1. **You add a share link** (`https://<org>.bitdocs.ai/share/d/<id>`) and choose the course folder it belongs in, for example `Mastering C#/Docs`.
2. **Lectern reads the document** through the same public API the bitdocs.ai page uses. A document is a tree of pages, and Lectern fetches every page in it.
3. **Each page becomes a Markdown file**, numbered in reading order, with its images and attachments saved next to it. Links between pages point at the local files.
4. **Changes are detected by content.** On each sync Lectern compares every page with the previous version and rewrites only what changed. New, updated, renamed and removed pages are recorded, along with the changed text.
5. **After each sync it can also:**
   - rebuild a PDF of each changed document
   - rebuild a searchable docs website
   - save a git snapshot, so any earlier version can be restored
6. **A built-in scheduler** repeats this daily, on weekdays or every few hours. Any run missed while the computer slept or Docker was stopped happens as soon as the container is back.

Lectern only reads public share links. It never needs your bit.ai account or password.

## Features

- **Complete copies:** pages, headings, lists, tables, code blocks, callouts, images and attachments (zip, PDF, …), with internal links rewritten to the local files.
- **Change tracking:**
  - `WHATS-NEW.md`, one page across all your courses
  - a `CHANGES.md` per document
  - the exact text that changed in every updated page
- **History:** a git snapshot after every sync. Browse any snapshot's diff, and restore a file as it was.
- **PDF export:** one PDF per document with a cover page and table of contents, rebuilt only when the document changes.
- **Docs website:** every document in one site, with a sidebar per course, full-text search, previous/next navigation and dark mode.
- **Tidy by design:** renamed or reordered pages, moved folders and removed images are cleaned up. Lectern only ever deletes files it created, and pages removed online are kept locally.
- **Safe to run anytime:** only one sync runs at a time, and a sync that fails partway keeps what it finished.
- **Web UI and command line**, plus an installable web app (PWA).

## Quick start

**Requirements:** Docker with Compose v2, and a folder for your courses that Lectern may write into.

```bash
git clone https://github.com/ibn-e-battuta/lectern.git
cd lectern
cp .env.example .env        # set COURSES_DIR, HOST_UID/HOST_GID (`id -u`, `id -g`) and TZ
docker compose up -d
```

Open **http://localhost:8790**, go to **Add**, paste a share link, choose a folder and click **Add & download**. The default schedule syncs every day at 09:00 in your `TZ`.

To update Lectern later:

```bash
git pull && docker compose up -d --build
```

## Using Lectern

### Web UI

| Tab | What you can do |
|---|---|
| **Docs** | Every document with its page count and last sync. **Read** it in the docs site, open its **PDF**, open it **Online**, **Sync** it now, rebuild its PDF, **Move** it to another folder, or **Remove** it (local files are kept) |
| **Add** | Paste a share link and choose a folder. Or paste a whole course page (its text or HTML) to find every document link on it and add the new ones together |
| **Search** | Full-text search across all downloaded documents |
| **What's new** | Everything new, updated, renamed or removed, newest first, with the changed text |
| **History** | One snapshot per sync; select one to see its changes |
| **Activity** | The sync log, live |
| **Settings** | The schedule (daily, weekdays or every N hours, or off) and the after-sync options |

The header shows whether a sync is running or when the next one is due. It also has **Sync now** and **Open docs site** buttons. The docs site is at **http://localhost:8790/site**.

### Command line

Every command runs in a one-off container, so it works whether or not the Lectern service is running:

```bash
docker compose run --rm lectern help
docker compose run --rm lectern add https://<org>.bitdocs.ai/share/d/<id> "Mastering C#/Docs"
docker compose run --rm lectern sync
docker compose run --rm lectern search dependency injection
docker compose run --rm lectern schedule weekdays 09:00 21:00
```

On macOS and Linux the `lectern` script in this repository is a shorter way to do the same thing. Link it onto your `PATH` (for example `ln -s "$PWD/lectern" /usr/local/bin/lectern`) and run `lectern start`, `lectern ui`, `lectern add …` or `lectern status`. It also handles the parts that need the host machine:
- `lectern scan --clipboard` reads copied text.
- `lectern scan ~/Downloads/page.html` scans a saved course page.

| Command | Does |
|---|---|
| `sync [LINK…]` | sync everything, or only the given links |
| `add LINK [FOLDER]` · `move LINK FOLDER` · `remove LINK` · `list` | manage documents (`remove` keeps the files) |
| `scan URL\|FILE\|-` | find document links on a page; add `--add --folder FOLDER` to add them all |
| `search WORDS…` | search downloaded documents |
| `site` · `pdf [DOC\|all]` | rebuild the docs site or PDFs |
| `history` · `history show SHA` · `history restore PATH SHA` | browse and restore snapshots |
| `schedule [daily\|weekdays TIME…\|every 6h\|off\|on]` | show or change the schedule |
| `config [site\|pdf\|git\|videos on\|off]` | after-sync options |
| `start` · `stop` · `status` · `logs` · `rebuild` · `ui` | manage the container (`lectern` script only) |

`FOLDER` is always a path inside your courses folder.

## What Lectern writes, and where

Lectern keeps **document content in your courses folder** and **everything else in its own data folder**. Your courses folder gets no extra top-level files.

**Courses folder** (`COURSES_DIR`, mounted at `/courses`): only inside the folders you choose for each document.

```
Mastering C#/
└── Docs/
    ├── 00 - Course Guide.md
    ├── 01 - Setting Up.md
    ├── 02 - Generics/              ← a page with sub-pages becomes a folder
    │   ├── 00 - Generics.md
    │   └── 01 - Constraints.md
    ├── assets/                      ← images and attachments
    ├── Course Guide.pdf
    ├── CHANGES.md                   ← this document's change log
    └── README.md                    ← table of contents
```

When several documents share one folder, each gets its own sub-folder named after its title.

**Data folder** (`./data`, mounted at `/data`):

| Path | Contents |
|---|---|
| `links.txt` | your documents: one share link per line, optionally `-> Folder` |
| `config.json` | schedule and after-sync options |
| `WHATS-NEW.md` | changes across all documents, newest first |
| `site/` | the docs website served at `/site` |
| `history.git/` | snapshots of the document folders (only those; nothing else in the courses folder is tracked) |
| `registry.json`, `sync.log` | where each document was last synced; the log |

Back up the data folder to keep your links, settings and history.

## Configuration

### Environment (`.env`)

| Variable | Default | Description |
|---|---|---|
| `COURSES_DIR` | *(required)* | Host path of your courses folder. Lectern needs write access. |
| `HOST_UID` / `HOST_GID` | `1000` / `1000` | The user and group the container runs as, so the files it writes belong to you. Use `id -u` and `id -g`. |
| `TZ` | `UTC` | Time zone for the schedule, for example `Europe/London`. |
| `LECTERN_PORT` | `8790` | Port of the web UI on the host. It is bound to `127.0.0.1` only. |

### Options (Settings tab or `lectern config`)

| Option | Default | When on |
|---|---|---|
| `site` | on | Rebuild the docs website after each sync |
| `pdf` | on | Rebuild the PDF of each changed document (headless Chromium inside the image) |
| `git` | on | Save a history snapshot after each sync |
| `videos` | on | Download video and audio files uploaded to a document. Hosted players such as YouTube stay as links |

Changes made in the UI or with `lectern schedule` / `lectern config` are saved to `data/config.json` and picked up by the running container within a minute.

## Running Lectern with Lyceum

[Lyceum](https://github.com/ibn-e-battuta/lyceum) is a self-hosted player for video courses. The two apps are independent and never call each other. They work together through the **courses folder they share**:

- **Lectern writes** each course's documents into that course's folder.
- **Lyceum reads** the same folder (read-only is enough) and shows them with the course. A `Docs` folder appears as its own section:
  - each document's **PDF** is a reading lesson;
  - the Markdown pages and attachments are that section's files.
- **New documents show up in Lyceum by themselves.** It watches the folder, or rescans every 30 minutes where file watching isn't available (network shares, Docker Desktop).

Either app can run alone, and either can be stopped without affecting the other.

### Both in one compose file

```yaml
# docker-compose.yml
services:
  lyceum:
    image: ghcr.io/ibn-e-battuta/lyceum:latest
    user: "1000:1000"                                   # `id -u`:`id -g`
    ports:
      - "8080:8080"
    volumes:
      - /path/to/courses:/courses:ro                    # Lyceum only reads
      - ./lyceum-config:/config
    environment:
      TZ: Europe/London
    restart: unless-stopped

  lectern:
    build: https://github.com/ibn-e-battuta/lectern.git#main
    user: "1000:1000"
    ports:
      - "127.0.0.1:8790:8790"
    volumes:
      - /path/to/courses:/courses                       # Lectern writes the documents
      - ./lectern-data:/data
    environment:
      TZ: Europe/London
    restart: unless-stopped
```

```bash
mkdir -p lyceum-config lectern-data && docker compose up -d
```

- **Lyceum:** http://localhost:8080. Create the admin account on first visit.
- **Lectern:** http://localhost:8790.

Start, stop or update either one alone with `docker compose up -d lyceum`, `docker compose stop lectern`, and so on.

### Keeping Lectern in its own folder

If you already run Lectern from a clone of this repository, include its compose file instead of copying the service definition:

```yaml
# docker-compose.yml, next to the lectern/ clone
name: courses               # use the same name in lectern/compose.yaml (it is "courses" by default)
include:
  - path: ./lectern/compose.yaml
    env_file: ./lectern/.env
services:
  lyceum:
    # … as above …
```

With the same project name, `docker compose up -d` in either folder manages the same Lectern container, so it never runs twice.

## Running without Docker

Lectern uses only the Python 3.9+ standard library. PDFs additionally need Google Chrome, Chromium or Microsoft Edge, and history needs git.

```bash
export LECTERN_ROOT=/path/to/courses LECTERN_DATA=$PWD/data
python3 app/lectern.py sync         # one sync
python3 app/lectern.py serve        # web UI on http://127.0.0.1:8790 and the scheduler
```

## Security and privacy

- The web UI has no login. It listens on `127.0.0.1` only, and it rejects state-changing requests that come from other websites. Don't publish its port to your network.
- It serves files from the courses folder read-only, and only from inside that folder.
- Lectern only connects to the bitdocs.ai address of each share link, bit.ai's file CDN, and any other host a document embeds images or files from.
- Your links, settings and history stay in the data folder. They are git-ignored in this repository.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "That link doesn't exist or isn't shared publicly" | The document isn't shared publicly. Ask for a public share link |
| Files are owned by root, or "permission denied" | Set `HOST_UID`/`HOST_GID` in `.env` to your own ids and recreate the container |
| Scheduled syncs don't happen | The container must be running at the scheduled time. Missed runs happen when it starts. On a desktop, let Docker start at login |
| A course page shows no links in **Add** | Course pages usually need a login. Paste the page's text or HTML instead of its address |
| Schedule times are off by hours | Set `TZ` in `.env` |

Logs: the **Activity** tab, `docker compose logs -f lectern`, or `data/sync.log`.

## Project layout

```
app/
├── lectern.py        command line, schedule and entry point
├── lectern_sync.py   download, conversion to Markdown, change tracking, clean-up
├── docsite.py        docs website and PDF export
├── history.py        git snapshots of the document folders
├── webui.py          web UI and JSON API
└── static/           icons and web app manifest
Dockerfile            Python 3.12, Chromium, git, fonts
compose.yaml          the lectern service
lectern               host script (macOS/Linux)
```
