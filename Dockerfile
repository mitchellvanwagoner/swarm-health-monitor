FROM python:3.11-alpine

LABEL maintainer="swarm-health-monitor"
LABEL description="Monitors torrent swarm health and prioritizes rare torrents"

# Install requests
RUN pip install --no-cache-dir requests

# Create app directory
WORKDIR /app

# Copy script
COPY swarm-health-monitor.py /app/

# Create config directory for state persistence
RUN mkdir -p /config

# Environment variables with defaults
ENV QBITTORRENT_URL="http://localhost:6767" \
    QBITTORRENT_USER="admin" \
    QBITTORRENT_PASS="adminadmin" \
    CHECK_INTERVAL_DAYS="30" \
    RUN_INTERVAL_HOURS="24" \
    STARTUP_DELAY_SECONDS="60" \
    CRITICAL_SEEDERS="2" \
    RARE_SEEDERS="5" \
    LOW_SEEDERS="10" \
    RESUME_CRITICAL="true" \
    RESUME_RARE="false" \
    SET_PRIORITIES="true" \
    STATE_FILE="/config/state.json" \
    DEBUG="false"

# Run as non-root user
RUN adduser -D -u 1000 keeper
USER keeper

ENTRYPOINT ["python", "-u", "/app/swarm-health-monitor.py"]