#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIND_ADDRESS=${WAVESENSE_BIND:-$(tailscale ip -4 | head -n 1)}
[ -n "$BIND_ADDRESS" ] || { echo 'Tailscale is not connected.' >&2; exit 1; }
exec /srv/team28/Team-28/.venv/bin/python "$APP_DIR/camera_preview.py" --bind "$BIND_ADDRESS" --port 8080 --lidar-mode 2d --radar-status-file /srv/team28/runtime/radar/status.json "$@"
