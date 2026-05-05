#!/bin/sh
# Container entrypoint — bridges runtime dirs onto the persistent volume
# (when one exists), then runs migrate + (seed-if-empty) and execs uvicorn.
# All Python logic lives in scripts/boot.py.
set -eu

# In production (Fly), a persistent volume is mounted at /data. The
# providers hardcode /local-storage, /sent-mail, /erp-sync-log — bridge
# each one onto a subdir of /data so writes survive container restarts.
# Locally (no /data mount), this whole block is a no-op.
if [ -d /data ]; then
  for dir in local-storage sent-mail erp-sync-log; do
    mkdir -p "/data/$dir"
    # Replace the empty image-built dir with a symlink to the volume.
    if [ ! -L "/$dir" ]; then
      rmdir "/$dir" 2>/dev/null || true
      ln -sfn "/data/$dir" "/$dir"
    fi
  done
fi

exec python -m scripts.boot
