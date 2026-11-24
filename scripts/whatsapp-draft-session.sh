#!/bin/sh
set -eu

state=$(mktemp -d)
x11vnc -display "$DISPLAY" -auth "$XAUTHORITY" -listen 0.0.0.0 -rfbport 5900 \
    -rfbauth /run/netcore/vnc-password -viewonly -forever -shared -o "$state/vnc.log" \
    -afteraccept "touch '$state/viewer-authenticated'" &
viewer_server=$!
trap 'kill "$viewer_server" 2>/dev/null || true; rm -f "$state/vnc.log" "$state/viewer-authenticated"; rmdir "$state"' EXIT

# A banner probe cannot authenticate. Start only after x11vnc accepts a viewer.
attempt=0
until [ -f "$state/viewer-authenticated" ]; do
    if ! kill -0 "$viewer_server" 2>/dev/null; then
        printf '%s\n' VNC_SERVER_START_FAILED >&2
        exit 1
    fi
    if [ "$attempt" -ge 60 ]; then
        printf '%s\n' VNC_VIEWER_CONNECTION_TIMEOUT >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done

python -m app.cli whatsapp-human-assisted "$@"
