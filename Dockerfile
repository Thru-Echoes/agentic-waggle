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

# Runtime dirs the providers expect (compose re-mounts these as volumes so
# writes persist across container restarts).
RUN mkdir -p /local-storage /sent-mail /erp-sync-log /gmail-fixtures /cache/huggingface

# Default command: boot the API. docker-compose overrides this per service.
EXPOSE 5001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5001"]
