# Fantasy hockey assistant: draft board (/) + mid-season assistant (/inseason)
FROM python:3.14-slim

# tzdata so TZ=America/New_York gives the right "today" for schedule windows.
# uv pinned so a rebuild resolves exactly what uv.lock says.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.7

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

WORKDIR /app
# dependencies first (cached layer), exactly as pinned in uv.lock;
# bytecode is compiled here because the container runs read-only
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY static ./static
COPY config ./config
COPY db ./db
RUN python -m compileall -q src

# runs as uid 1000 so files it writes to the bind-mounted ./data are yours;
# the code itself stays root-owned (the app can't rewrite itself)
RUN useradd --uid 1000 --create-home app && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/inseason/leagues', timeout=5)"

# --no-server-header: don't advertise the server version;
# --limit-concurrency: a burst of requests queues instead of exhausting the box
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--limit-concurrency", "32", "--timeout-keep-alive", "15"]
