# lectern: mirrors bitdocs.ai course docs into your courses folder, with a web UI.
FROM python:3.12-slim-bookworm

# chromium -> PDF export, git -> history snapshots, fonts -> readable PDFs, tzdata -> local schedule times
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium git tzdata ca-certificates \
      fonts-noto-core fonts-noto-color-emoji fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/ /app/

ENV LECTERN_ROOT=/courses \
    LECTERN_DATA=/data \
    LECTERN_IN_DOCKER=1 \
    LECTERN_PORT=8790 \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/api/state', timeout=4)" || exit 1

WORKDIR /courses
ENTRYPOINT ["python3", "-I", "/app/lectern.py"]
# Web UI + scheduler. One-off commands: docker compose run --rm lectern <command>
CMD ["serve"]
