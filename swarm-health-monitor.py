#!/usr/bin/env python3
"""
Swarm Health Monitor
Monitors torrent swarm health and prioritizes rare torrents to keep them alive.
Tracks last-checked time per torrent and only rechecks after configurable interval.
"""

import requests
import json
import os
import logging
import time
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

# =============================================================================
# CONFIGURATION - Set via environment variables
# =============================================================================

def get_env_bool(key, default):
    """Get boolean from environment variable."""
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ('true', '1', 'yes')

def get_env_int(key, default):
    """Get integer from environment variable."""
    val = os.environ.get(key)
    if val is None:
        return default
    return int(val)

def get_env_float(key, default):
    """Get float from environment variable."""
    val = os.environ.get(key)
    if val is None:
        return default
    return float(val)

# Connection settings
QBITTORRENT_URL = os.environ.get("QBITTORRENT_URL", "http://localhost:6767")
QBITTORRENT_USER = os.environ.get("QBITTORRENT_USER", "admin")
QBITTORRENT_PASS = os.environ.get("QBITTORRENT_PASS", "adminadmin")

# Timing settings
CHECK_INTERVAL_DAYS = get_env_float("CHECK_INTERVAL_DAYS", 30.0)  # Days between checks per torrent
RUN_INTERVAL_HOURS = get_env_float("RUN_INTERVAL_HOURS", 24.0)    # Hours between script runs
STARTUP_DELAY_SECONDS = get_env_int("STARTUP_DELAY_SECONDS", 60)  # Wait for qBittorrent to be ready

# Thresholds for rarity classification
CRITICAL_SEEDERS = get_env_int("CRITICAL_SEEDERS", 1)
RARE_SEEDERS = get_env_int("RARE_SEEDERS", 2)
LOW_SEEDERS = get_env_int("LOW_SEEDERS", 5)

# Actions to take
RESUME_CRITICAL = get_env_bool("RESUME_CRITICAL", True)
RESUME_RARE = get_env_bool("RESUME_RARE", False)
SET_PRIORITIES = get_env_bool("SET_PRIORITIES", True)

# Paths
STATE_FILE = os.environ.get("STATE_FILE", "/config/state.json")
LOG_LEVEL = logging.DEBUG if get_env_bool("DEBUG", False) else logging.INFO

# =============================================================================
# GLOBALS
# =============================================================================

shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logging.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


def setup_logging():
    """Configure logging output."""
    logging.basicConfig(
        level=LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )


class StateManager:
    """Manages persistent state for tracking last-checked times."""
    
    def __init__(self, state_file):
        self.state_file = Path(state_file)
        self.state = self._load_state()
    
    def _load_state(self):
        """Load state from file or return empty state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    logging.info(f"Loaded state for {len(data.get('torrents', {}))} torrents")
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logging.warning(f"Failed to load state file: {e}")
        return {"torrents": {}, "last_full_run": None}
    
    def save_state(self):
        """Save current state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
            logging.debug(f"Saved state for {len(self.state['torrents'])} torrents")
        except IOError as e:
            logging.error(f"Failed to save state: {e}")
    
    def get_last_checked(self, torrent_hash):
        """Get last checked timestamp for a torrent."""
        return self.state["torrents"].get(torrent_hash, {}).get("last_checked")
    
    def needs_check(self, torrent_hash, interval_days):
        """Check if torrent needs to be checked based on interval."""
        last_checked = self.get_last_checked(torrent_hash)
        if last_checked is None:
            return True
        
        last_checked_dt = datetime.fromisoformat(last_checked)
        next_check = last_checked_dt + timedelta(days=interval_days)
        return datetime.now() >= next_check
    
    def update_torrent(self, torrent_hash, torrent_name, seeder_count, classification):
        """Update state for a torrent after checking."""
        self.state["torrents"][torrent_hash] = {
            "name": torrent_name,
            "last_checked": datetime.now().isoformat(),
            "last_seeder_count": seeder_count,
            "last_classification": classification
        }
    
    def cleanup_removed_torrents(self, current_hashes):
        """Remove state entries for torrents that no longer exist."""
        current_set = set(current_hashes)
        removed = [h for h in self.state["torrents"] if h not in current_set]
        for h in removed:
            del self.state["torrents"][h]
        if removed:
            logging.info(f"Cleaned up state for {len(removed)} removed torrents")
    
    def get_stats(self):
        """Get statistics about tracked torrents."""
        stats = {"CRITICAL": 0, "RARE": 0, "LOW": 0, "HEALTHY": 0, "UNKNOWN": 0}
        for data in self.state["torrents"].values():
            classification = data.get("last_classification", "UNKNOWN")
            stats[classification] = stats.get(classification, 0) + 1
        return stats


class QBittorrentAPI:
    """Simple qBittorrent Web API client."""
    
    def __init__(self, url, username, password):
        self.url = url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self._authenticated = False
    
    def login(self):
        """Authenticate with qBittorrent."""
        try:
            response = self.session.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=30
            )
            if response.text == "Ok.":
                self._authenticated = True
                logging.info("Successfully authenticated with qBittorrent")
                return True
            else:
                logging.error(f"Authentication failed: {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            logging.error(f"Connection error: {e}")
            return False
    
    def get_torrents(self):
        """Get list of all torrents with their info."""
        if not self._authenticated:
            if not self.login():
                return []
        
        try:
            response = self.session.get(
                f"{self.url}/api/v2/torrents/info",
                timeout=60
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to get torrent list: {e}")
            self._authenticated = False
            return []
    
    def get_torrent_trackers(self, torrent_hash):
        """Get tracker info for a specific torrent."""
        if not self._authenticated:
            if not self.login():
                return []
        
        try:
            response = self.session.get(
                f"{self.url}/api/v2/torrents/trackers",
                params={"hash": torrent_hash},
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.debug(f"Failed to get trackers for {torrent_hash}: {e}")
            return []
    
    def resume_torrent(self, torrent_hash):
        """Resume a paused torrent."""
        if not self._authenticated:
            if not self.login():
                return False
        
        try:
            response = self.session.post(
                f"{self.url}/api/v2/torrents/resume",
                data={"hashes": torrent_hash},
                timeout=30
            )
            return response.status_code == 200
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to resume torrent: {e}")
            return False
    
    def set_top_priority(self, torrent_hash):
        """Move torrent to top of queue."""
        if not self._authenticated:
            if not self.login():
                return False
        
        try:
            response = self.session.post(
                f"{self.url}/api/v2/torrents/topPrio",
                data={"hashes": torrent_hash},
                timeout=30
            )
            return response.status_code == 200
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to set priority: {e}")
            return False
    
    def set_rare_priority(self, torrent_hash):
        """Set rare priority: move to top, then decrease by one (so it's below critical)."""
        if not self._authenticated:
            if not self.login():
                return False
        
        try:
            # First move to top
            response = self.session.post(
                f"{self.url}/api/v2/torrents/topPrio",
                data={"hashes": torrent_hash},
                timeout=30
            )
            if response.status_code != 200:
                return False
            
            # Then decrease by one so it's below critical torrents
            response = self.session.post(
                f"{self.url}/api/v2/torrents/decreasePrio",
                data={"hashes": torrent_hash},
                timeout=30
            )
            return response.status_code == 200
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to set rare priority: {e}")
            return False
    
    def set_low_priority(self, torrent_hash):
        """Set low priority: move to top, then decrease by two (below critical and rare)."""
        if not self._authenticated:
            if not self.login():
                return False
        
        try:
            # First move to top
            response = self.session.post(
                f"{self.url}/api/v2/torrents/topPrio",
                data={"hashes": torrent_hash},
                timeout=30
            )
            if response.status_code != 200:
                return False
            
            # Decrease twice so it's below critical and rare
            for _ in range(2):
                response = self.session.post(
                    f"{self.url}/api/v2/torrents/decreasePrio",
                    data={"hashes": torrent_hash},
                    timeout=30
                )
                if response.status_code != 200:
                    return False
            
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to set low priority: {e}")
            return False


def get_seeder_count(qbt, torrent):
    """Get the most accurate seeder count for a torrent."""
    tracker_seeds = torrent.get('num_complete', 0)
    connected_seeds = torrent.get('num_seeds', 0)
    
    our_contribution = 1 if torrent.get('state') in ['uploading', 'stalledUP', 'forcedUP'] else 0
    
    if tracker_seeds > 0:
        return tracker_seeds
    elif connected_seeds > 0:
        return connected_seeds + our_contribution
    else:
        trackers = qbt.get_torrent_trackers(torrent['hash'])
        max_seeds = 0
        for tracker in trackers:
            if tracker.get('num_seeds', 0) > max_seeds:
                max_seeds = tracker['num_seeds']
        return max_seeds if max_seeds > 0 else our_contribution


def classify_torrent(seeder_count):
    """Classify torrent by rarity."""
    if seeder_count <= CRITICAL_SEEDERS:
        return "CRITICAL"
    elif seeder_count <= RARE_SEEDERS:
        return "RARE"
    elif seeder_count <= LOW_SEEDERS:
        return "LOW"
    else:
        return "HEALTHY"


def format_size(size_bytes):
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def run_check(qbt, state_manager):
    """Run a single check cycle."""
    logging.info("=" * 60)
    logging.info(f"Starting check run at {datetime.now()}")
    logging.info(f"Check interval: {CHECK_INTERVAL_DAYS} days per torrent")
    logging.info("=" * 60)
    
    # Get all torrents
    torrents = qbt.get_torrents()
    if not torrents:
        logging.warning("No torrents found or failed to retrieve list.")
        return
    
    logging.info(f"Found {len(torrents)} total torrents")
    
    # Cleanup state for removed torrents
    current_hashes = [t['hash'] for t in torrents]
    state_manager.cleanup_removed_torrents(current_hashes)
    
    # Determine which torrents need checking
    torrents_to_check = []
    skipped_count = 0
    
    for torrent in torrents:
        if state_manager.needs_check(torrent['hash'], CHECK_INTERVAL_DAYS):
            torrents_to_check.append(torrent)
        else:
            skipped_count += 1
    
    logging.info(f"Torrents needing check: {len(torrents_to_check)}")
    logging.info(f"Torrents skipped (checked recently): {skipped_count}")
    
    if not torrents_to_check:
        logging.info("No torrents need checking this run.")
        stats = state_manager.get_stats()
        logging.info(f"Current distribution from last checks: {stats}")
        return
    
    # Check torrents that need it
    critical_torrents = []
    rare_torrents = []
    low_torrents = []
    checked_count = 0
    
    for torrent in torrents_to_check:
        if shutdown_requested:
            logging.info("Shutdown requested, saving state...")
            break
        
        seeder_count = get_seeder_count(qbt, torrent)
        classification = classify_torrent(seeder_count)
        
        state_manager.update_torrent(
            torrent['hash'],
            torrent['name'],
            seeder_count,
            classification
        )
        checked_count += 1
        
        torrent_info = {
            'hash': torrent['hash'],
            'name': torrent['name'][:60] + '...' if len(torrent['name']) > 60 else torrent['name'],
            'seeders': seeder_count,
            'size': format_size(torrent.get('size', 0)),
            'state': torrent.get('state', 'unknown'),
        }
        
        if classification == "CRITICAL":
            critical_torrents.append(torrent_info)
            logging.info(f"  CRITICAL [{seeder_count} seeds]: {torrent_info['name']}")
        elif classification == "RARE":
            rare_torrents.append(torrent_info)
            logging.info(f"  RARE [{seeder_count} seeds]: {torrent_info['name']}")
        elif classification == "LOW":
            low_torrents.append(torrent_info)
            logging.debug(f"  LOW [{seeder_count} seeds]: {torrent_info['name']}")
        
        # Save state periodically
        if checked_count % 50 == 0:
            state_manager.save_state()
            logging.debug(f"Checkpoint: checked {checked_count}/{len(torrents_to_check)}")
    
    # Report findings
    stats = state_manager.get_stats()
    logging.info("")
    logging.info(f"Overall distribution (all tracked torrents): {stats}")
    logging.info(f"This run - Critical: {len(critical_torrents)}, Rare: {len(rare_torrents)}, Low: {len(low_torrents)}")
    
    # Take actions
    actions_taken = 0

    if SET_PRIORITIES and (critical_torrents or rare_torrents or low_torrents):
        logging.info("")
        logging.info("Adjusting queue priorities...")
        # Process in order: LOW first, then RARE, then CRITICAL
        # This ensures final priority order: CRITICAL > RARE > LOW > HEALTHY
        for t in low_torrents:
            if qbt.set_low_priority(t['hash']):
                logging.debug(f"  Set LOW priority: {t['name']}")
                actions_taken += 1
        for t in rare_torrents:
            if qbt.set_rare_priority(t['hash']):
                logging.debug(f"  Set RARE priority: {t['name']}")
                actions_taken += 1
        for t in critical_torrents:
            if qbt.set_top_priority(t['hash']):
                logging.debug(f"  Set CRITICAL priority: {t['name']}")
                actions_taken += 1
    
    if RESUME_RARE and rare_torrents:
        logging.info("")
        logging.info("Checking rare torrents for resume...")
        for t in rare_torrents:
            if t['state'] in ['pausedUP', 'pausedDL', 'stoppedUP', 'stoppedDL']:
                logging.info(f"  Resuming: {t['name']}")
                if qbt.resume_torrent(t['hash']):
                    actions_taken += 1

    if RESUME_CRITICAL and critical_torrents:
        logging.info("")
        logging.info("Checking critical torrents for resume...")
        for t in critical_torrents:
            if t['state'] in ['pausedUP', 'pausedDL', 'stoppedUP', 'stoppedDL']:
                logging.info(f"  Resuming: {t['name']}")
                if qbt.resume_torrent(t['hash']):
                    actions_taken += 1
    
    # Save final state
    state_manager.save_state()
    
    logging.info("")
    logging.info(f"Checked {checked_count} torrents, actions taken: {actions_taken}")
    logging.info(f"This run - Critical: {len(critical_torrents)}, Rare: {len(rare_torrents)}, Low: {len(low_torrents)}")
    logging.info(f"Run completed at {datetime.now()}")
    logging.info("=" * 60)


def main():
    global shutdown_requested
    
    setup_logging()
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logging.info("Swarm health monitor starting up...")
    logging.info(f"qBittorrent URL: {QBITTORRENT_URL}")
    logging.info(f"Check interval: {CHECK_INTERVAL_DAYS} days per torrent")
    logging.info(f"Run interval: {RUN_INTERVAL_HOURS} hours")
    logging.info(f"Seeder thresholds - Critical: <={CRITICAL_SEEDERS}, Rare: <={RARE_SEEDERS}, Low: <={LOW_SEEDERS}")
    
    # Initial delay to let qBittorrent start
    if STARTUP_DELAY_SECONDS > 0:
        logging.info(f"Waiting {STARTUP_DELAY_SECONDS}s for qBittorrent to be ready...")
        time.sleep(STARTUP_DELAY_SECONDS)
    
    # Initialize
    state_manager = StateManager(STATE_FILE)
    qbt = QBittorrentAPI(QBITTORRENT_URL, QBITTORRENT_USER, QBITTORRENT_PASS)
    
    run_interval_seconds = RUN_INTERVAL_HOURS * 3600
    
    while not shutdown_requested:
        try:
            run_check(qbt, state_manager)
        except Exception as e:
            logging.error(f"Error during check run: {e}", exc_info=True)
        
        if shutdown_requested:
            break
        
        logging.info(f"Next run in {RUN_INTERVAL_HOURS} hours. Sleeping...")
        
        # Sleep in small increments to respond to shutdown signals
        sleep_until = time.time() + run_interval_seconds
        while time.time() < sleep_until and not shutdown_requested:
            time.sleep(min(60, sleep_until - time.time()))
    
    logging.info("Swarm Health Monitor shut down cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())