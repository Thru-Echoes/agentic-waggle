# ---------------------------------------------------------------------------
# agentic-mcp-crm — single image for api / worker / seed / mcp entrypoints.
# Compose overrides CMD per service.
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # sentence-transformers cache lives here; compose mounts a named volume
    # so model weights aren't re-downloaded every rebuild.
    HF_HOME=/cache/huggingface \
    TRANSFORMERS_CACHE=/cache/huggingface

WORKDIR /app

# System deps:
#   libpq5       — psycopg2 runtime
#   build-essential + libpq-dev — needed only if psycopg2-binary fails and we
#       fall back to building from source. Kept in build stage; stripped in
#       a multi-stage later if image size matters.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so source-code edits don't bust the dep cache.
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Pre-download the sentence-transformers model into the image. Without
# this, the first request after a fresh container start downloads ~80 MB
# from HuggingFace and times out — the model lives in HF_HOME so this
# layer caches it where the runtime expects to find it.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Copy the source tree. Order follows likelihood-of-change (least → most)
# to keep layer caching useful. Each dir must exist at build time — placeholder
# __init__.py files are checked in so the image still builds while the app
# scaffolding is being filled out.
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY providers /app/providers
COPY db /app/db
COPY seed /app/seed
COPY agents /app/agents
COPY mcp_servers /app/mcp_servers
COPY app /app/app
COPY scripts /app/scripts
COPY tests /app/tests

# Runtime dirs the providers expect. Locally, compose re-mounts these as
# volumes so writes persist across container restarts. In production, the
# storage / sent-mail / erp-sync-log dirs are typically remounted onto a
# persistent volume (e.g., Fly volume at /data/<dir>); gmail-fixtures is a
# read-only dataset baked into the image, so we copy it in directly.
RUN mkdir -p /local-storage /sent-mail /erp-sync-log /cache/huggingface
COPY gmail-fixtures /gmail-fixtures

# Default command: run migrations, conditionally seed, then exec uvicorn.
# docker-compose overrides this per service (the `seed` service uses an
# empty entrypoint so it can run alembic / seed.generate / pytest directly).
EXPOSE 5001
CMD ["./scripts/entrypoint.sh"]
