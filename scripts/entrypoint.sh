#!/bin/sh
# Container entrypoint.
#
# 1. Bridge the provider directories onto the persistent volume when one is
#    mounted at /data (Fly). Locally, with no /data, this block is a no-op.
# 2. Fix ownership so the non-root `waggle` user can write there.
# 3. Drop privileges and hand off to scripts/boot.py (migrate, seed-if-empty,
#    exec uvicorn). All Python logic lives in boot.py.
#
# The image's runtime stage starts as root solely so step 2 can chown a
# volume that Docker/Fly mount as root; nothing after `setpriv` runs as root.
set -eu

if [ -d /data ]; then
  for dir in local-storage sent-mail erp-sync-log; do
    mkdir -p "/data/$dir"
    if [ ! -L "/$dir" ]; then
      rmdir "/$dir" 2>/dev/null || true
      ln -sfn "/data/$dir" "/$dir"
    fi
  done
  chown -R waggle:waggle /data
fi

if [ "$(id -u)" = "0" ]; then
  exec setpriv --reuid=waggle --regid=waggle --init-groups python -m scripts.boot
fi
exec python -m scripts.boot
