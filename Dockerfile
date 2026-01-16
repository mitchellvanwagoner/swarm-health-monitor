FROM python:3.11-alpine

LABEL maintainer="swarm-health-monitor"
LABEL description="Monitors torrent swarm health and prioritizes rare torrents"

# Install requests and su-exec for user switching
RUN apk add --no-cache su-exec && \
    pip install --no-cache-dir requests

# Create app directory
WORKDIR /app

# Copy files
COPY swarm-health-monitor.py /app/
COPY entrypoint.sh /app/

RUN chmod +x /app/entrypoint.sh

# Create config directory
RUN mkdir -p /config

# Environment variables with defaults
ENV PUID="1000" \
    PGID="1000" \
    QBITTORRENT_URL="http://localhost:6767" \
    QBITTORRENT_USER="admin" \
    QBITTORRENT_PASS="adminadmin" \
    CHECK_INTERVAL_DAYS="30" \
    RUN_INTERVAL_HOURS="24" \
    STARTUP_DELAY_SECONDS="60" \
    CRITICAL_SEEDERS="1" \
    RARE_SEEDERS="3" \
    LOW_SEEDERS="5" \
    RESUME_CRITICAL="true" \
    RESUME_RARE="false" \
    SET_PRIORITIES="true" \
    STATE_FILE="/config/state.json" \
    DEBUG="false"

ENTRYPOINT ["/app/entrypoint.sh"]
