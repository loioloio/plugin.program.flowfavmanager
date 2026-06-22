# -*- coding: utf-8 -*-
"""Favourites persistence: load/save of favourites.xml and profile (.json) storage."""
import datetime
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import xbmcaddon
import xbmcvfs

from resources.lib.common import PATHS, log_debug


class FavouriteEntry:
    """A single favourite item."""

    def __init__(self, name, thumb, url):
        self.name = name
        self.thumb = thumb
        self.url = url

    @classmethod
    def from_xml_element(cls, element):
        return cls(
            name=element.get('name', ''),
            thumb=element.get('thumb', ''),
            url=element.text or '',
        )

    def to_xml_string(self):
        # Quotes are escaped in the attributes because name/thumb sit inside double-quoted attrs.
        name = escape(self.name, {"'": '&apos;', '"': '&quot;'})
        thumb = escape(self.thumb, {"'": '&apos;', '"': '&quot;'})
        return f'    <favourite name="{name}" thumb="{thumb}">{escape(self.url)}</favourite>'


class FavouritesEngine:
    """Loading, saving and backup of the favourites list."""

    def __init__(self):
        self.entries = []

    def load(self):
        self.entries = []
        if not os.path.exists(PATHS['favourites']):
            return False
        try:
            root = ET.parse(PATHS['favourites']).getroot()
        except (ET.ParseError, OSError) as e:
            log_debug(f'Could not read favourites.xml: {e}')
            return False
        if root.tag == 'favourites':
            self.entries = [FavouriteEntry.from_xml_element(c) for c in root if c.tag == 'favourite']
        return True

    def load_original(self):
        """Reload favourites from disk and return the list."""
        self.load()
        return self.entries

    def save(self, xml_content=None):
        """Write favourites to disk atomically. Serializes self.entries when xml_content is None."""
        if xml_content is None:
            xml_content = self.generate_xml(self.entries)

        # Back up the current file before overwriting; a failed backup must not abort the save.
        try:
            if os.path.exists(PATHS['favourites']):
                shutil.copy(PATHS['favourites'], PATHS['backup'])
        except OSError as e:
            log_debug(f'Could not back up favourites.xml: {e}')

        # Write to .tmp, flush, then os.replace (atomic and overwrites on both Windows and POSIX),
        # so favourites.xml is never left half-written if Kodi dies during the write.
        tmp_path = PATHS['favourites'] + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass  # network/Android filesystems may not support fsync; os.replace stays atomic
            os.replace(tmp_path, PATHS['favourites'])
            return True
        except OSError as e:
            log_debug(f'Could not save favourites.xml: {e}')
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def generate_xml(self, entries):
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<favourites>']
        lines += [entry.to_xml_string() for entry in entries]
        lines.append('</favourites>')
        return '\n'.join(lines) + '\n'

    def enrich_missing_icons(self):
        """Assign an icon to favourites that lack one, derived from their addon id."""
        count = 0
        for entry in self.entries:
            if entry.thumb:
                continue
            match = re.search(r'^plugin://([^/]+)/', entry.url)
            if not match:
                # Shortcuts stored as RunAddon("plugin.id") also carry a usable addon id.
                match = re.search(r'RunAddon\("?([^")\s]+)"?\)', entry.url)
            if not match:
                continue
            addon_id = match.group(1)
            try:
                icon = xbmcaddon.Addon(addon_id).getAddonInfo('icon')
                if icon:
                    entry.thumb = icon
                    entry.auto_icon = True
                    count += 1
            except RuntimeError:
                # Disabled addon: Addon() raises, but icon.png is still on disk.
                fallback = f'special://home/addons/{addon_id}/icon.png'
                if xbmcvfs.exists(fallback):
                    entry.thumb = fallback
                    entry.auto_icon = True
                    count += 1
        return count


def get_profiles():
    """Return the list of available profiles (.json files), sorted by name."""
    if not os.path.exists(PATHS['profiles']):
        return []

    profiles = []
    for f in os.listdir(PATHS['profiles']):
        if not f.endswith('.json'):
            continue
        path = os.path.join(PATHS['profiles'], f)
        try:
            with open(path, 'r', encoding='utf-8') as file:
                data = json.load(file)
            # A failed mtime read (network share, Android metadata perms) must not drop a
            # perfectly valid profile, so it gets its own guard with an empty date fallback.
            try:
                date_str = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
            except OSError:
                date_str = ''
            profiles.append({
                'filename': f,
                'name': data.get('name', f[:-len('.json')]),
                'date': date_str,
                'entries': data.get('entries', []),
            })
        except Exception as e:
            # Best-effort read of arbitrary profile files: a corrupt or hand-edited .json (bad
            # syntax, or a top-level value that isn't an object) must not bring down the whole
            # list, so log it and skip.
            log_debug(f'Skipping unreadable profile {f}: {e}')
    return sorted(profiles, key=lambda x: x['name'])


def save_profile(name, entries):
    """Save the list of entries as a profile. Returns the .json filename actually written."""
    safe = ''.join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
    # Cap the length: the full name is kept in the JSON; only the filename is bounded so a long
    # name can't push the absolute path past Windows' 260-char MAX_PATH.
    safe = safe[:50].strip()
    # A symbols-only name would sanitize to an empty string (a hidden, collidable ".json" file),
    # so it gets a unique filename derived from the save time.
    if not safe:
        safe = 'profile_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = safe + '.json'
    path = os.path.join(PATHS['profiles'], filename)

    data = {
        'name': name,
        'entries': [{'name': e.name, 'thumb': e.thumb, 'url': e.url} for e in entries],
    }

    os.makedirs(PATHS['profiles'], exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return filename


def load_profile(filename):
    """Load entries from a profile."""
    path = os.path.join(PATHS['profiles'], filename)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [FavouriteEntry(e.get('name', ''), e.get('thumb', ''), e.get('url', '')) for e in data.get('entries', [])]


def delete_profile(filename):
    """Delete a profile file. Returns True if a file was removed."""
    path = os.path.join(PATHS['profiles'], filename)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
