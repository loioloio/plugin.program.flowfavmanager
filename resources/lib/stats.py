"""Anonymous telemetry: global installation counter.

Sends a single ping per installation without transmitting any
identifier. The server accumulates a global total; records older than
30 days are automatically deleted.
Deduplication lives in the client (stats.json), which is never transmitted.
"""
import json
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcvfs

_API_URL = "https://gpcbfvgxwesvlezaning.supabase.co"
# Supabase anon key: public by design (it ships in every client). It is not a secret; abuse is
# kept out server-side via RLS (the table has no SELECT policy) and rate limiting, not by hiding it.
_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdwY2Jmdmd4d2VzdmxlemFuaW5nIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM0ODQ1MjYsImV4cCI6MjA4OTA2MDUyNn0.ipnXlrez3yA6CWriqwJU4OzVRYn2nNicpOQSuzpBy-w"
_TABLE = "pings"
_PING_INTERVAL_HOURS = 24
_DATA_FILE = 'stats.json'


def _data_path():
    profile = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return os.path.join(profile, _DATA_FILE)


def _load_data():
    path = _data_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_data(data):
    path = _data_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError as e:
        # Non-critical: if it fails to save, the next startup will resend the ping.
        xbmc.log(f'[Flow FavManager] stats: could not save {_DATA_FILE}: {e}', xbmc.LOGWARNING)


def _send_ping(kind):
    """POST an anonymous ping to Supabase. True if accepted by the server."""
    import requests

    headers = {
        'apikey': _API_KEY,
        'Authorization': f'Bearer {_API_KEY}',
        'Content-Type': 'application/json',
        # The table does not have a SELECT policy: requesting the inserted row
        # back would fail due to RLS. return=minimal avoids that reread.
        'Prefer': 'return=minimal',
    }
    payload = {
        'addon': xbmcaddon.Addon().getAddonInfo('id'),
        'kind': kind,
    }
    resp = requests.post(
        f'{_API_URL}/rest/v1/{_TABLE}',
        headers=headers,
        json=payload,
        timeout=10,
    )
    if resp.status_code in (200, 201, 204):
        return True
    xbmc.log(f'[Flow FavManager] stats: ping {kind} rejected: HTTP {resp.status_code}', xbmc.LOGWARNING)
    return False


def _do_pings():
    try:
        data = _load_data()

        # Save after each successful ping: if the next one fails, what was
        # already sent is recorded and won't be resent (prevents overcounting).
        if not data.get('install_sent') and _send_ping('install'):
            data['install_sent'] = True
            _save_data(data)

        hours_since = (time.time() - data.get('last_ping', 0)) / 3600
        if hours_since >= _PING_INTERVAL_HOURS and _send_ping('daily'):
            data['last_ping'] = time.time()
            _save_data(data)
    except Exception as e:
        # Daemon thread: no failure (network, addon) should propagate.
        xbmc.log(f'[Flow FavManager] stats: ping failed: {e}', xbmc.LOGWARNING)


def ping():
    """Launches pending pings in the background. Does not block the caller."""
    try:
        threading.Thread(target=_do_pings, daemon=True).start()
    except RuntimeError as e:
        xbmc.log(f'[Flow FavManager] stats: could not start ping thread: {e}', xbmc.LOGWARNING)
