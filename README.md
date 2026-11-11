# Lectern

Keeps local copies of bit.ai / bitdocs.ai course docs up to date in your courses folder: Markdown, images, attachments, PDFs and a browsable docs site.
Lectern runs in Docker, with a web UI at **http://localhost:8790**.

## Run it
First time: `cp .env.example .env` and set your courses folder, user/group id and timezone.

**On its own**
```bash
lectern start        # or: cd ~/Developer/Lectern && docker compose up -d
lectern ui           # opens http://localhost:8790
```
**Together with Lyceum**
```bash
cd ~/Developer/course-stack && docker compose up -d
```
Both use the compose project name `courses`, so lectern never runs twice. `lectern start` / `lectern stop` only touch Lectern, and the stack reuses a lectern container that's already running.

The container restarts by itself whenever Docker Desktop starts. For automatic syncs, let Docker Desktop open at login (Settings > General > "Start Docker Desktop when you sign in").

## Web UI
| Tab | What it does |
|---|---|
| Docs | Every doc with page count and last sync; Read (docs site), PDF, Online, Sync, Rebuild PDF, Move, Remove |
| Add | Paste a share link and pick a folder; or paste a whole course page to find every doc link on it |
| Search | Full-text search across all downloaded docs |
| What's new | New, updated and renamed pages, with the changed text |
| History | A snapshot after every sync; click one to see its diff |
| Activity | Live sync log |
| Settings | Schedule (daily / weekdays / every N hours, on/off) and the after-sync options |

The header shows whether a sync is running or when the next one is due, plus **Sync now** and **Open docs site**.
The UI only listens on `127.0.0.1`, and it refuses requests sent by other websites.

## Command line
`lectern help` lists everything. Each command runs in a throwaway container:
```bash
lectern add LINK "Course Name/Docs"     lectern list           lectern sync
lectern scan ~/Downloads/page.html      lectern search WORDS   lectern news
lectern schedule weekdays 9:00 21:00    lectern history        lectern config pdf off
lectern status | logs | stop | rebuild
```

## Layout
| Path | What |
|---|---|
| `app/` | the Python app (stdlib only); the only thing copied into the image |
| `data/` | your data: `links.txt`, `config.json`, `registry.json`, `history.git`, `sync.log` (mounted at `/data`) |
| `Dockerfile` | Python 3.12 + Chromium (PDFs) + git (history) + fonts |
| `compose.yaml` | the `lectern` service: mounts the courses folder at `/courses` and `data/` at `/data`; runs as your Mac user |
| `.env` | `COURSES_DIR`, user/group id, `TZ`, `BITDOCS_PORT` |
| `lectern` | the Mac command (linked from `/opt/homebrew/bin/lectern`) |
| `app/static/` | icons (SVG, favicon.ico, PWA and apple-touch sizes) and the web app manifest |

Output that goes into the courses folder: each doc's folder (Markdown, `assets/`, PDF, `CHANGES.md`, `README.md`), `Docs Site/` and `WHATS-NEW.md`.

## Behaviour worth knowing
- **Changing a doc's folder** (UI Move, `lectern move`, or editing `links.txt`) moves its files, PDF included. Nothing is re-downloaded.
- **Several docs in one folder** each get their own subfolder.
- **Renamed or reordered pages**: old files, images and empty folders are cleaned up. Only files lectern created are removed.
- **Pages removed online** are noted in `CHANGES.md`; the local copy is kept.
- **Only one sync runs at a time**: UI, schedule and command line share a lock.
- **Missed schedule times** (Mac asleep, Docker stopped) run as soon as the container is up.
- **A failed run** keeps what it finished; the next run picks up the rest.
- **Folders** in `links.txt` must be inside the courses folder, because that's all the container can see.
- **History** tracks only the doc folders and `WHATS-NEW.md`, never your videos or other files.
