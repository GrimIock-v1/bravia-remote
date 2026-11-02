#!/bin/sh
# Make /config writable for PUID:PGID (Unraid default 99:100), then drop root.
set -e
PUID="${PUID:-99}"
PGID="${PGID:-100}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$CONFIG_DIR"
  chown -R "$PUID:$PGID" "$CONFIG_DIR" 2>/dev/null || true
  exec su-exec "$PUID:$PGID" "$@"
fi
exec "$@"
