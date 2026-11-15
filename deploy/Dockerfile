FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg yt-dlp \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's signature challenge
# and ffmpeg to merge the separate video and audio streams it is offered.
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip \
    && pip install ".[all,server]" "yt-dlp[default]"

# Default DB lives outside the image — mount a host directory at /data.
ENV MEDIA_ARCHIVIST_DB_FILE=/data/index.json
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["sh", "-c", "media-archivist serve --db-file ${MEDIA_ARCHIVIST_DB_FILE} --host 0.0.0.0 --port 8000"]
