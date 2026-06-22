# -*- coding: utf-8 -*-
"""Favourite URL resolution and ListItem construction for the plugin views."""
import re
import urllib.parse

import xbmcgui

from resources.lib import common


def resolve_target_url(url, base_url, current_id):
    """Translate a favourite's stored URL into a Kodi-navigable (target_url, is_folder).

    Favourites store both navigable paths (plugin://) and commands (RunAddon, ActivateWindow…).
    Commands cannot be navigated, so they are routed to the '/execute' bridge, which runs them
    with executebuiltin.
    """
    url = url or ''

    def bridge(command):
        return f'{base_url}execute?cmd=' + urllib.parse.quote(command)

    if url.lower().startswith('plugin://'):
        target, is_folder = url, True
    elif url.startswith('RunAddon('):
        match = re.search(r'RunAddon\("?([^")\s]+)"?\)', url)
        if match and match.group(1).startswith('plugin.'):
            target, is_folder = f'plugin://{match.group(1)}/', True
        else:
            # Scripts (script.*) and other non-plugin addons aren't navigable plugin:// sources;
            # run them through the execution bridge instead of opening a bogus directory.
            target, is_folder = bridge(url), False
    elif url.lower().startswith('script://'):
        match = re.match(r'^script://([^/]+)/?', url, re.IGNORECASE)
        cmd = f'RunAddon("{match.group(1)}")' if match else url
        target, is_folder = bridge(cmd), False
    else:
        target, is_folder = bridge(url), False

    # Anti-recursion: a favourite that navigates back into this addon would loop onto itself.
    # Wrap it in a Container.Update through the execution bridge.
    if current_id and is_folder and current_id in target:
        target, is_folder = bridge(f'Container.Update({target})'), False
    return target, is_folder


def build_list_item(entry):
    """Build a favourite's ListItem together with its target URL and whether it is a folder."""
    li = xbmcgui.ListItem(label=entry.name)
    li.setArt({'thumb': entry.thumb, 'icon': entry.thumb})

    target_url, is_folder = resolve_target_url(entry.url, common.BASE_URL, common.ADDON_ID)

    # Command shortcuts (anything that isn't a navigable folder) must be marked non-playable,
    # or Kodi treats the click as media playback and shows a "playback failed" error + spinner.
    if not is_folder:
        li.setProperty('IsPlayable', 'false')

    li.setInfo('video', {'title': entry.name, 'plot': entry.url})
    return li, target_url, is_folder


def normalize_url(url):
    """Normalize a URL captured from a ListItem into a valid Kodi favourite format."""
    if not url:
        return url
    match = re.match(r'^script://([^/]+)/?', url)
    if match:
        return f'RunAddon("{match.group(1)}")'
    return url
