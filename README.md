# Swarm Health Monitor

A Docker container that monitors your qBittorrent torrents, identifies those with few seeders, and automatically prioritizes them to keep rare content alive.

## Features

- **Smart scheduling**: Tracks when each torrent was last checked and only rechecks after a configurable interval
- **Persistent state**: Remembers torrent health across restarts
- **Low impact**: Spreads checks over time instead of hammering the API
- **Auto-resume**: Optionally resumes paused critical/rare torrents
- **Priority management**: Moves rare torrents to the top of the queue
- **Graceful shutdown**: Saves state when container stops

## Quick Start

Add to your `docker-compose.yml`:

```yaml
  swarm-health-monitor:
    build: 'ghcr.io/mitchellvanwagoner/swarm-health-monitor:latest'
    container_name: 'swarm-health-monitor'
    restart: 'unless-stopped'
    environment:
      - 'PUID=$PUID'
      - 'PGID=$PGID'
      - 'UMASK=$UMASK'
      - 'TZ=$TZ'
      - 'QBITTORRENT_URL=http://qbittorrentvpn:6767'
      - 'QBITTORRENT_USER=admin'
      - 'QBITTORRENT_PASS=your-password-here'
      - 'CHECK_INTERVAL_DAYS=30'
      - 'RUN_INTERVAL_HOURS=24'
    volumes:
      - '/mnt/user/appdata/swarm-health-monitor:/config'
    network_mode: 'service:qbittorrentvpn'
    depends_on:
      qbittorrentvpn:
        condition: service_healthy
```

Then:

```bash
# Create directories
mkdir -p /mnt/user/appdata/swarm-health-monitor/config

# Copy Dockerfile and swarm-health-monitor.py to /mnt/user/appdata/swarm-health-monitor/

# Start it
docker compose up -d swarm-health-monitor

# View logs
docker logs -f swarm-health-monitor
```

## How It Works

1. **On startup**, waits for qBittorrent to be ready (configurable delay)
2. **Each run cycle** (default: every 24 hours):
   - Fetches all torrents from qBittorrent
   - Checks which torrents haven't been checked in `CHECK_INTERVAL_DAYS`
   - For those torrents, queries seeder count from trackers
   - Classifies as CRITICAL/RARE/LOW/HEALTHY
   - Takes configured actions (resume, priority boost)
   - Saves state to `/config/state.json`
3. **Sleeps** until next run cycle

This means with default settings:
- The script wakes up every 24 hours
- Each torrent gets its swarm health checked once per month
- Over time, all torrents get checked, but spread out to minimize load

## Environment Variables

### Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `QBITTORRENT_URL` | `http://localhost:6767` | qBittorrent Web UI URL |
| `QBITTORRENT_USER` | `admin` | Web UI username |
| `QBITTORRENT_PASS` | `adminadmin` | Web UI password |

### Timing

| Variable | Default | Description |
|----------|---------|-------------|
| `CHECK_INTERVAL_DAYS` | `30` | Days between health checks per torrent |
| `RUN_INTERVAL_HOURS` | `24` | Hours between script run cycles |
| `STARTUP_DELAY_SECONDS` | `60` | Seconds to wait for qBittorrent on startup |

### Seeder Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `CRITICAL_SEEDERS` | `2` | Torrents with ≤ this many seeders are critical |
| `RARE_SEEDERS` | `5` | Torrents with ≤ this many seeders are rare |
| `LOW_SEEDERS` | `10` | Torrents with ≤ this many seeders are low |

### Actions

| Variable | Default | Description |
|----------|---------|-------------|
| `RESUME_CRITICAL` | `true` | Auto-resume paused critical torrents |
| `RESUME_RARE` | `false` | Auto-resume paused rare torrents |
| `SET_PRIORITIES` | `true` | Boost queue priority for rare torrents |

### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `STATE_FILE` | `/config/state.json` | Path to persistent state file |
| `DEBUG` | `false` | Enable debug logging |

## Example Configurations

### Conservative (minimal intervention)
```yaml
environment:
  - CHECK_INTERVAL_DAYS=60      # Check each torrent every 2 months
  - RUN_INTERVAL_HOURS=168      # Run weekly
  - RESUME_CRITICAL=true
  - RESUME_RARE=false
  - SET_PRIORITIES=false        # Don't touch priorities
```

### Aggressive (keep everything alive)
```yaml
environment:
  - CHECK_INTERVAL_DAYS=7       # Check weekly
  - RUN_INTERVAL_HOURS=6        # Run 4x daily
  - CRITICAL_SEEDERS=3
  - RARE_SEEDERS=10
  - RESUME_CRITICAL=true
  - RESUME_RARE=true
  - SET_PRIORITIES=true
```

### Private tracker focused
```yaml
environment:
  - CHECK_INTERVAL_DAYS=14      # Check every 2 weeks
  - CRITICAL_SEEDERS=1          # Only you seeding = critical
  - RARE_SEEDERS=3
  - RESUME_CRITICAL=true
  - RESUME_RARE=true
```

## State File

The state file (`/config/state.json`) tracks:
- Last checked timestamp per torrent
- Last known seeder count
- Last classification

Example:
```json
{
  "torrents": {
    "abc123...": {
      "name": "Some.Movie.2020.1080p",
      "last_checked": "2025-01-15T03:00:00",
      "last_seeder_count": 2,
      "last_classification": "CRITICAL"
    }
  }
}
```

If you want to force a recheck of all torrents, delete the state file and restart the container.

## Logs

View logs with:
```bash
docker logs swarm-health-monitor
docker logs -f swarm-health-monitor  # follow
```

Sample output:
```
2025-01-15 03:00:00 - INFO - ============================================================
2025-01-15 03:00:00 - INFO - Starting check run at 2025-01-15 03:00:00
2025-01-15 03:00:00 - INFO - Check interval: 30.0 days per torrent
2025-01-15 03:00:00 - INFO - ============================================================
2025-01-15 03:00:01 - INFO - Found 847 total torrents
2025-01-15 03:00:01 - INFO - Torrents needing check: 28
2025-01-15 03:00:01 - INFO - Torrents skipped (checked recently): 819
2025-01-15 03:00:05 - INFO -   CRITICAL [1 seeds]: Rare.Documentary.2019
2025-01-15 03:00:12 - INFO -   RARE [4 seeds]: Old.Album.FLAC
2025-01-15 03:00:30 - INFO - Overall distribution (all tracked torrents): {'CRITICAL': 12, 'RARE': 34, 'LOW': 89, 'HEALTHY': 712}
2025-01-15 03:00:30 - INFO - Checked 28 torrents, actions taken: 3
```

## Troubleshooting

**Container exits immediately**
- Check logs for connection errors
- Verify qBittorrent URL is reachable from the container's network
- Increase `STARTUP_DELAY_SECONDS` if qBittorrent isn't ready

**"No torrents need checking this run"**
- This is normal if all torrents were recently checked
- Delete `/config/state.json` to force a full recheck

**Seeder counts seem wrong**
- Tracker data can be delayed or inaccurate
- The script uses the best available data (tracker > connected > DHT)

**Priority changes don't seem to work**
- Only affects queued torrents
- If you have unlimited active torrents (`-1`), priority has no effect
