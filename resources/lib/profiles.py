# -*- coding: utf-8 -*-
"""Favourite profiles management menu (create, import, export, load, rename, delete).

Profile storage lives in database.py; this module only holds the user interaction.
"""
import datetime
import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcgui

from resources.lib.common import CloseAddon, get_string, log_audit, log_debug
from resources.lib.database import (
    FavouriteEntry,
    FavouritesEngine,
    delete_profile,
    get_profiles,
    load_profile,
    save_profile,
)


def _safe_filename(name):
    return ''.join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()


def _write_favourites_xml(entries, path):
    root = ET.Element('favourites')
    for e in entries:
        item = ET.SubElement(root, 'favourite')
        item.set('name', e.name)
        item.set('thumb', e.thumb)
        item.text = e.url
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def _create_profile():
    kb = xbmc.Keyboard('', get_string(30035))
    kb.doModal()
    if not kb.isConfirmed() or not kb.getText():
        return False
    name = kb.getText()
    engine = FavouritesEngine()
    engine.load()
    if save_profile(name, engine.entries):
        xbmcgui.Dialog().notification(get_string(30262), get_string(30263).format(name), xbmcgui.NOTIFICATION_INFO)
        log_audit('PROFILE_CREATED', f"Profile '{name}' created with {len(engine.entries)} items")
        xbmc.executebuiltin('Container.Refresh')
        return True
    return False


def _import_xml_profile():
    path = xbmcgui.Dialog().browse(1, get_string(30250), 'files', '.xml')
    if not path:
        return False
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        xbmcgui.Dialog().textviewer(get_string(30269), str(e))
        return False

    entries = [FavouriteEntry.from_xml_element(c) for c in root if c.tag == 'favourite']
    log_debug(f'Importing XML: {len(entries)} favourites found')
    if not entries:
        xbmcgui.Dialog().notification(get_string(30044), get_string(30264), xbmcgui.NOTIFICATION_ERROR)
        return False

    kb = xbmc.Keyboard(get_string(30265), get_string(30266))
    kb.doModal()
    if not kb.isConfirmed():
        return False
    p_name = kb.getText() or get_string(30265)
    save_profile(p_name, entries)
    xbmcgui.Dialog().notification(get_string(30267), get_string(30268).format(len(entries)), xbmcgui.NOTIFICATION_INFO)
    log_audit('PROFILE_IMPORTED', f"Profile '{p_name}' imported from XML ({len(entries)} items)")
    xbmc.executebuiltin('Container.Refresh')
    return True


def _export_current_favourites():
    dest = xbmcgui.Dialog().browse(3, get_string(30388), 'files')
    if not dest:
        return
    engine = FavouritesEngine()
    engine.load()
    file_name = f"favourites_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xml"
    try:
        _write_favourites_xml(engine.entries, os.path.join(dest, file_name))
        xbmcgui.Dialog().notification(get_string(30270), file_name, xbmcgui.NOTIFICATION_INFO)
    except OSError as e:
        xbmcgui.Dialog().ok(get_string(30271), str(e))


def _profile_actions(prof):
    opts = [get_string(30272), get_string(30112), get_string(30273), get_string(30119), '« ' + get_string(30430)]
    act = xbmcgui.Dialog().select(f"{get_string(30274)}: {prof['name']}", opts)

    if act == 0:  # Load as the active favourites
        if xbmcgui.Dialog().yesno(get_string(30275), get_string(30276).format(prof['name'])):
            engine = FavouritesEngine()
            engine.entries = load_profile(prof['filename'])
            engine.save()
            xbmcgui.Dialog().notification(get_string(30277), get_string(30278), xbmcgui.NOTIFICATION_INFO)
            log_audit('PROFILE_LOADED', f"Profile '{prof['name']}' loaded as the active favourites")
            return True

    elif act == 1:  # Rename
        kb = xbmc.Keyboard(prof['name'], get_string(30279))
        kb.doModal()
        if kb.isConfirmed() and kb.getText() and kb.getText() != prof['name']:
            new_name = kb.getText()
            entries = load_profile(prof['filename'])
            new_filename = save_profile(new_name, entries)
            # Only delete the old file if the new name maps to a different file; otherwise
            # save_profile already overwrote it and deleting would wipe the just-saved profile.
            if new_filename != prof['filename']:
                delete_profile(prof['filename'])
            xbmcgui.Dialog().notification(get_string(30280), get_string(30281).format(new_name), xbmcgui.NOTIFICATION_INFO)
            log_audit('PROFILE_RENAMED', f"Profile renamed from '{prof['name']}' to '{new_name}'")

    elif act == 2:  # Export to XML
        dest = xbmcgui.Dialog().browse(3, get_string(30388), 'files')
        if dest:
            try:
                _write_favourites_xml(load_profile(prof['filename']), os.path.join(dest, f"{_safe_filename(prof['name'])}.xml"))
                xbmcgui.Dialog().notification(get_string(30270), get_string(30282).format(f"{_safe_filename(prof['name'])}.xml"), xbmcgui.NOTIFICATION_INFO)
            except OSError as e:
                xbmcgui.Dialog().ok(get_string(30271), str(e))

    elif act == 3:  # Delete
        if xbmcgui.Dialog().yesno(get_string(30283), get_string(30284).format(prof['name'])):
            delete_profile(prof['filename'])
            log_audit('PROFILE_DELETED', f"Profile '{prof['name']}' deleted")
    return False


def run_profiles_menu():
    while True:
        profiles = get_profiles()
        display_list = [get_string(30258), get_string(30259), get_string(30260)]
        for p in profiles:
            items_label = get_string(30203).format(len(p['entries']))
            display_list.append(f"{p['name']} | [I]{p['date']}[/I] | {items_label}")
        display_list.append(get_string(30520))

        sel = xbmcgui.Dialog().select(get_string(30261), display_list)
        if sel == len(display_list) - 1:
            raise CloseAddon()
        if sel == -1:
            return

        if sel == 0:
            if _create_profile():
                return
        elif sel == 1:
            if _import_xml_profile():
                return
        elif sel == 2:
            _export_current_favourites()
        else:
            if _profile_actions(profiles[sel - 3]):
                return
