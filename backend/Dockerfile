# build-essential (needed to compile chromadb's native deps) stays isolated to
# this stage; the runtime image below only gets the compiled site-packages, so
# an RCE against the app doesn't hand an attacker a compiler for free.
FROM python:3.11.9-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11.9-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV RESOLVEFLOW_DB_PATH=/app/data/resolveflow.db
ENV RESOLVEFLOW_NOW=2026-06-01

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 resolveflow

COPY --from=builder /install /usr/local
COPY --chown=resolveflow:resolveflow backend ./backend
COPY --chown=resolveflow:resolveflow docs/policies ./docs/policies

RUN mkdir -p /app/data && chown resolveflow:resolveflow /app/data
USER resolveflow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["sh", "-c", "if [ ! -s \"$RESOLVEFLOW_DB_PATH\" ]; then python -m backend.db.seed_demo_dashboard --db-path \"$RESOLVEFLOW_DB_PATH\"; fi; uvicorn backend.api.main:app --host 0.0.0.0 --port 8000"]
