# -*- coding: utf-8 -*-
"""Backup and restore favourites to/from a user-chosen XML file."""
import datetime
import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcgui

from resources.lib.common import get_string, translatePath
from resources.lib.database import FavouriteEntry, FavouritesEngine


def _create_backup():
    engine = FavouritesEngine()
    engine.load()
    if not engine.entries:
        xbmcgui.Dialog().ok(get_string(30247), get_string(30248))
        return

    default_name = 'favourites_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    kb = xbmc.Keyboard(default_name, get_string(30217))
    kb.doModal()
    if not kb.isConfirmed() or not kb.getText():
        return

    folder = xbmcgui.Dialog().browse(0, get_string(30388), 'files')
    if not folder:
        return

    path = os.path.join(translatePath(folder), kb.getText() + '.xml')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(engine.generate_xml(engine.entries))
        xbmcgui.Dialog().notification(get_string(30249), kb.getText(), xbmcgui.NOTIFICATION_INFO, 3000)
    except OSError as e:
        xbmcgui.Dialog().ok(get_string(30044), str(e))


def _restore_backup():
    file_path = xbmcgui.Dialog().browse(1, get_string(30250), 'files', '.xml')
    if not file_path:
        return

    full_path = translatePath(file_path)
    if not os.path.exists(full_path):
        xbmcgui.Dialog().ok(get_string(30044), get_string(30251))
        return

    try:
        root = ET.parse(full_path).getroot()
    except ET.ParseError as e:
        xbmcgui.Dialog().ok(get_string(30044), get_string(30257) + "\n" + str(e))
        return

    if root.tag != 'favourites':
        xbmcgui.Dialog().ok(get_string(30044), get_string(30252))
        return

    loaded = [FavouriteEntry.from_xml_element(c) for c in root if c.tag == 'favourite']
    if not loaded:
        xbmcgui.Dialog().ok(get_string(30044), get_string(30253))
        return

    if xbmcgui.Dialog().yesno(get_string(30108), get_string(30254).format(len(loaded))):
        engine = FavouritesEngine()
        engine.save(engine.generate_xml(loaded))
        xbmcgui.Dialog().notification(get_string(30255), get_string(30256).format(len(loaded)), xbmcgui.NOTIFICATION_INFO, 3000)


def run_backup_menu():
    """Backup/restore menu reachable from the main menu."""
    choice = xbmcgui.Dialog().select(get_string(30216), [get_string(30245), get_string(30246)])
    if choice == 0:
        _create_backup()
    elif choice == 1:
        _restore_backup()
