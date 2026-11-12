# Lectern

Lectern keeps local copies of course documents published on **bit.ai / bitdocs.ai** and keeps them up to date. Give it the share links of the documents that come with your courses. It downloads every page, image and attachment into the matching course folder, checks for changes on a schedule, and tells you exactly what changed.

It's a self-hosted web app (.NET 10, Blazor) that runs as one Docker container. It works on its own or next to [Lyceum](https://github.com/ibn-e-battuta/lyceum), the self-hosted video course player.

- [How it works](#how-it-works)
- [Features](#features)
- [Quick start (Docker)](#quick-start-docker)
- [Using Lectern](#using-lectern)
- [What Lectern writes, and where](#what-lectern-writes-and-where)
- [Configuration](#configuration)
- [Running Lectern with Lyceum](#running-lectern-with-lyceum)
- [Security and privacy](#security-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Running from source](#running-from-source)

## How it works

```
 bitdocs.ai share links ──▶  Lectern  ──▶  your courses folder
 (one per document)          │              Course A/Docs/  ← Markdown, images, attachments, PDF
                             │              Course B/Docs/
                             └──▶  /config
                                   database, history, sign-in keys, log
```

1. **You add a share link** (`https://<org>.bitdocs.ai/share/d/<id>`) and choose the course folder it belongs in, for example `Mastering C#/Docs`.
2. **Lectern reads the document** through the same public API the bitdocs.ai page uses. A document is a tree of pages, and Lectern fetches every page in it.
3. **Each page becomes a Markdown file**, numbered in reading order, with its images and attachments saved next to it. Links between pages point at the local files.
4. **Changes are detected by content.** On each sync Lectern compares every page with the previous version and rewrites only what changed. New, updated, renamed and removed pages are recorded, along with the changed text.
5. **After each sync it can also:**
   - rebuild a PDF of each changed document;
   - save a history snapshot, so any earlier version can be restored.
6. **A built-in scheduler** repeats this daily, on weekdays or every few hours. A run missed while the container was stopped happens as soon as it's back.

Lectern only reads public share links. It never needs your bit.ai account or password.

## Features

- **Complete copies:** pages, headings, lists, tables, code blocks, callouts, images and attachments (zip, PDF, …), with internal links rewritten to the local files.
- **Reader:** every document in one place, with an outline per course, previous/next, copy buttons on code and dark mode. It works on phones.
- **Search** across all downloaded pages.
- **Change tracking:** *What's new* across all documents, a `CHANGES.md` per document, and the exact text that changed in every updated page.
- **History:** a snapshot after every sync. Browse what each one changed and put any file back as it was.
- **PDF export:** one PDF per document with a cover page and contents, rebuilt only when the document changes.
- **Tidy by design:** renamed or reordered pages, moved folders and removed images are cleaned up. Lectern only deletes files it created, and pages removed online are kept locally.
- **Safe to run any time:** one sync at a time (documents added meanwhile are queued), and a sync that fails partway keeps what it finished.
- **Sign-in:** an admin account, created on first visit.

## Quick start (Docker)

**Requirements:** Docker with Compose v2, and a folder for your courses that Lectern may write into.

```yaml
# docker-compose.yml
services:
  lectern:
    image: ghcr.io/ibn-e-battuta/lectern:latest
    user: "1000:1000"                         # your user and group id (`id -u`, `id -g`)
    ports:
      - "8790:8080"
    volumes:
      - /path/to/your/courses:/courses        # documents are written here
      - ./config:/config                      # database, history, sign-in keys
    environment:
      TZ: Europe/London                       # the schedule uses this time zone
    restart: unless-stopped
```

```bash
mkdir -p config && docker compose up -d
```

Open **http://localhost:8790** (or `http://<server>:8790`) and create the admin account. Then go to **Add**, paste a share link, choose a folder and click **Add & download**. The default schedule syncs every day at 09:00 in your `TZ`.

To update: `docker compose pull && docker compose up -d`. Database changes are applied automatically on startup.

[`docker-compose.yml`](docker-compose.yml) in this repository has the same service with the optional settings commented in.

### Images and releases

Images for **amd64** and **arm64** are published to GitHub Container Registry:

| Tag | Contains |
|---|---|
| `ghcr.io/ibn-e-battuta/lectern:latest` | the newest release |
| `:0.2.0`, `:0.2` | a specific release (pin one of these for predictable upgrades) |
| `:edge` | the latest commit on `main` |

To release: bump `<Version>` in `Lectern.csproj`, commit, then push an annotated tag such as `v0.2.0` (`git tag -a v0.2.0 && git push origin main v0.2.0`). The workflow runs the tests, publishes the versioned images and `:latest`, and creates a [GitHub Release](https://github.com/ibn-e-battuta/lectern/releases) whose notes are the tag's message. Pushes to `main` only update `:edge`.

## Using Lectern

| Page | What it's for |
|---|---|
| **Documents** | Every document with its folder, page count and last sync. Read it, open the PDF or the online version, sync it, rebuild its PDF, move it to another folder, or stop syncing it (its files stay) |
| **Add** | Add a share link, or find every link on a course page. Paste the page's address, or, when the page needs a login, its copied text or source |
| **Read** | Read any downloaded document, with an outline per course |
| **Search** | Search every downloaded page. All words must match |
| **What's new** | New, updated, renamed and removed pages from each sync, newest first, with the changed text |
| **History** | Snapshots after each sync: what changed, and **Restore this version** for any file |
| **Activity** | The live log |
| **Settings** | Schedule, after-sync options, your password |

**Sync now** in the top bar syncs everything. The status next to it shows what's running, or when the next sync is due.

## What Lectern writes, and where

Lectern keeps **document content in your courses folder** and **everything else in `/config`**. Your courses folder gets no extra top-level files.

**Courses folder** (mounted at `/courses`): only inside the folders you choose.

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
    ├── README.md                    ← table of contents
    ├── .sync-state.json             ← what was downloaded (Lyceum also uses it to recognise the folder)
    └── .raw/                        ← each page's original JSON, for the reader and PDF
```

When several documents share one folder, each gets its own sub-folder named after its title.

**`/config`:**

| Path | Contents |
|---|---|
| `lectern.db` | accounts, documents, settings and What's new |
| `history.git/` | snapshots of the document folders (only those; nothing else in the courses folder is tracked) |
| `keys/` | keys for the sign-in cookie. Keep them, or everyone is signed out |
| `lectern.log` | the activity log |

Back up `/config` to keep your documents list, settings and history.

## Configuration

Environment variables (the `__` is two underscores):

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `Etc/UTC` | Time zone for the schedule and dates |
| `LECTERN_ADMIN_USER`, `LECTERN_ADMIN_PASSWORD` | – | Create the admin on first start instead of using the setup page |
| `Lectern__TrustForwardedHeaders` | `false` | Set to `true` behind a reverse proxy so Lectern sees the real client address and scheme |
| `Lectern__CoursesRoot` | `/courses` | Where documents are written |
| `Lectern__ConfigFolder` | `/config` | Database, history, keys and log |
| `Lectern__Chromium` | the image's Chromium | Browser used to print PDFs |

**Settings** in the app:

| Option | Default | When on |
|---|---|---|
| Sync automatically | daily 09:00 | Every day or on weekdays at one or more times, or every few hours |
| Rebuild PDFs | on | Rebuild the PDF of each changed document |
| Save a history snapshot | on | Snapshot the document folders after each sync |
| Download uploaded videos | on | Download video and audio files uploaded to a document. Hosted players such as YouTube stay as links |

Lectern's interface uses a live **WebSocket** connection (Blazor Server), so a reverse proxy must forward WebSocket upgrades, the same as for Lyceum.

## Running Lectern with Lyceum

The two apps are independent and never call each other. They work together through the **courses folder they share**:

- **Lectern writes** each course's documents into that course's folder.
- **Lyceum reads** the same folder (read-only is enough) and shows them with the course. A `Docs` folder appears as its own section, after the course's other sections:
  - each document's **Markdown pages** are reading lessons, and links between them open the next page in Lyceum's player;
  - the whole-document **PDF**, `README.md` and `CHANGES.md` are that section's downloads.
- **New documents show up in Lyceum by themselves.** It watches the folder, or rescans every 30 minutes where file watching isn't available (network shares, Docker Desktop).

Both in one compose file:

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
    image: ghcr.io/ibn-e-battuta/lectern:latest
    user: "1000:1000"
    ports:
      - "8790:8080"
    volumes:
      - /path/to/courses:/courses                       # Lectern writes the documents
      - ./lectern-config:/config
    environment:
      TZ: Europe/London
    restart: unless-stopped
```

```bash
mkdir -p lyceum-config lectern-config && docker compose up -d
```

- **Lyceum:** http://localhost:8080. Create its admin account on first visit.
- **Lectern:** http://localhost:8790. Create its admin account on first visit.

Start, stop or update either one alone with `docker compose up -d lyceum`, `docker compose stop lectern`, and so on.

## Security and privacy

- Every page and file needs a signed-in account. The first account is created on the setup page (or from `LECTERN_ADMIN_USER`/`LECTERN_ADMIN_PASSWORD`). After 8 wrong passwords, that username and address are blocked for 15 minutes. Changing your password signs out your other sessions.
- Lectern serves files only from the document folders it manages, never other files in the courses folder, and never hidden files such as `.sync-state.json`.
- Lectern only connects to the bitdocs.ai address of each share link, bit.ai's file CDN, any other host a document embeds images or files from, and a course page you ask it to scan.
- Document content is shown as HTML that Lectern builds itself. Scripts and `javascript:` links from documents are never included.
- If you expose Lectern to the internet, put it behind HTTPS.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "That link doesn't exist or isn't shared publicly" | The document isn't shared publicly. Ask for a public share link |
| Files are owned by root, or "permission denied" | Set `user:` to your own ids (`id -u`, `id -g`) and recreate the container |
| Scheduled syncs don't happen | The container must be running at the scheduled time. Missed runs happen when it starts |
| A course page shows no links in **Add** | Course pages usually need a login. Paste the page's text or source instead of its address |
| Schedule times are off by hours | Set `TZ` |
| The page doesn't respond behind a proxy | Forward WebSocket upgrades, and set `Lectern__TrustForwardedHeaders=true` |

Logs: the **Activity** page, `docker compose logs -f lectern`, or `/config/lectern.log`.

## Running from source

Requirements: the .NET 10 SDK. PDF export needs Google Chrome, Chromium or Microsoft Edge; it's found automatically.

```bash
Lectern__CoursesRoot=~/Courses dotnet run
```

That serves http://localhost:5000 and keeps its data in `./config`. Set `ASPNETCORE_URLS` to use another address.

Tests:

```bash
dotnet test tests/Lectern.Tests
```

The converter is also checked against Markdown from earlier versions of Lectern. Those fixtures are real course pages, so they are not in the repository: set `LECTERN_GOLDEN` to a folder with `expected.json` and the page files to run that test.

To change the database schema:

```bash
dotnet tool restore
dotnet ef migrations add <Name> -o Data/Migrations
```

Project layout:

```
Program.cs                 startup: database, sign-in, file serving
Components/                Blazor pages (Documents, Add, Read, Search, What's new, History, Activity, Settings)
Services/
├── DocSync.cs             download a document, write pages and assets, track changes
├── MarkdownConverter.cs   bitdocs pages (ProseMirror JSON) to Markdown
├── HtmlRenderer.cs        the same pages as HTML for the reader and PDF
├── SyncRunner.cs          one sync at a time, then PDFs and a snapshot
├── Scheduler.cs           the automatic schedule
├── PdfBuilder.cs          PDF export with headless Chromium
├── HistoryService.cs      snapshots (LibGit2Sharp)
└── …                      search, link scanner, settings, accounts
Data/                      EF Core database and migrations
tests/Lectern.Tests/       unit tests
```
