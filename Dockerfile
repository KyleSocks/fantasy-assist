# Fantasy hockey assistant: draft board (/) + mid-season assistant (/inseason)
FROM python:3.12-slim

# tzdata so TZ=America/New_York gives the right "today" for schedule windows
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=America/New_York

WORKDIR /app
# dependencies first (cached layer), exactly as pinned in uv.lock
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY static ./static
COPY config ./config
COPY db ./db

# runs as uid 1000 so files it writes to the bind-mounted ./data are yours
RUN useradd --uid 1000 --create-home app && mkdir -p /app/data && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/inseason/leagues', timeout=5)"

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
