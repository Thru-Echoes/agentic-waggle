# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Waggle — one Dockerfile, three stages.
#
#   base     runtime dependencies from uv.lock, model weights baked in,
#            source tree copied, non-root user created.
#   test     base + the dev dependency group + tests/. Used by the compose
#            `seed` service (`make test`, `make seed`, `make migrate`).
#   runtime  base + entrypoint. The last stage, so it is what `docker build .`
#            and `fly deploy` produce.
#
# Dependencies install from the lock (`uv sync --frozen`): the versions that
# run in production are the versions that were tested. torch resolves from
# the CPU-only PyTorch index on Linux (see pyproject.toml), which keeps the
# image at a fraction of the CUDA build.
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS base

ARG WAGGLE_UID=10001
ARG WAGGLE_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # sentence-transformers cache; compose mounts a named volume here so
    # the weights baked into the image survive rebuilds.
    HF_HOME=/cache/huggingface \
    # uv installs into a fixed virtualenv on PATH, outside /app, so the
    # source tree can be bind-mounted without hiding the environment.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# libpq5: psycopg2-binary bundles libpq, but keeping the system library makes
# `psql`-less debugging via python work on any wheel. curl: health probes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${WAGGLE_GID}" waggle \
    && useradd --uid "${WAGGLE_UID}" --gid waggle --create-home --shell /usr/sbin/nologin waggle

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

# Dependencies first so source edits do not bust the layer cache.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Pre-download the embedding model into the image. Without this, the first
# request after a cold start downloads ~80 MB and times out the probe.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Source tree, least-changing first.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY providers ./providers
COPY db ./db
COPY seed ./seed
COPY agents ./agents
COPY mcp_servers ./mcp_servers
COPY app ./app
COPY scripts ./scripts

# Provider roots. Locally compose bind-mounts these; on Fly the entrypoint
# bridges them onto the /data volume. gmail-fixtures is a read-only dataset
# baked in so the image is self-contained.
COPY gmail-fixtures /gmail-fixtures
RUN mkdir -p /local-storage /sent-mail /erp-sync-log /app/.reports \
    && chown -R waggle:waggle /app /local-storage /sent-mail /erp-sync-log /gmail-fixtures /cache/huggingface

# Build provenance, surfaced by GET /health.
ARG GIT_SHA=unknown
ARG BUILT_AT=unknown
ENV WAGGLE_GIT_SHA=${GIT_SHA} \
    WAGGLE_BUILT_AT=${BUILT_AT}

EXPOSE 5001


# ---------------------------------------------------------------------------
FROM base AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
# The environment stays root-owned and read-only for the test user; only the
# tree the tests write to (reports, pytest caches) needs to be writable.
# chown-ing /opt/venv would copy the whole environment into a new layer.
COPY --chown=waggle:waggle tests ./tests
USER waggle
CMD ["pytest", "tests", "-q"]


# ---------------------------------------------------------------------------
FROM base AS runtime

# The entrypoint starts as root only to fix ownership of the mounted volume,
# then drops to the `waggle` user before running migrations and uvicorn.
USER root
CMD ["./scripts/entrypoint.sh"]
