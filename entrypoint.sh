#!/bin/sh

# Default to UID/GID 1000 if not specified
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Starting with UID: $PUID, GID: $PGID"

# Create group and user with specified IDs
addgroup -g "$PGID" nobody 2>/dev/null
adduser -D -u "$PUID" -G nobody nobody 2>/dev/null

# Ensure /config exists and has correct ownership
mkdir -p /config
chown -R "$PUID:$PGID" /config

# Run the script as the specified user
exec su-exec "$PUID:$PGID" python -u /app/swarm-health-monitor.py
