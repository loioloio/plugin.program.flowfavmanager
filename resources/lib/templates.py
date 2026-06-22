# -*- coding: utf-8 -*-
"""Predefined item templates (Kodi sections, commands) and their editor."""
import datetime
import json
import os

import xbmc
import xbmcgui

from resources.lib.common import CloseAddon, PATHS, get_string, log_debug, translatePath

# Fixed categories → translatable string id. User-created categories are not here and are
# shown with their formatted key.
MAP_CATEGORIES = {
    'secciones_kodi': 30452,
    'comandos_sistema': 30453,
}

# Fixed item names (key in English, as stored) → translatable string id.
MAP_ITEMS = {
    'Movies': 30400, 'TV (PVR)': 30401, 'Live TV (PVR)': 30401,
    'TV Guide (EPG)': 30402, 'Music': 30403, 'Pictures': 30404,
    'Power / Restart Menu': 30405, 'Update Library (Video)': 30406,
    'Clean Library (Video)': 30407, 'TV Shows': 30417, 'Home': 30418,
    'Radio': 30419, 'Music Videos': 30420, 'Weather': 30421,
    'Settings': 30422, 'File Manager': 30423, 'System Info': 30424,
    'Event Log': 30425, 'Addons (Browser)': 30426,
    'Reload Skin': 30427, 'Skin Settings': 30428,
}


def load_templates():
    try:
        with open(PATHS['templates'], 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        log_debug(f'Error loading templates: {e}')
        return {'secciones_kodi': [], 'comandos_sistema': []}


def save_templates(data):
    try:
        with open(PATHS['templates'], 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except OSError as e:
        log_debug(f'Error saving templates: {e}')
        return False


def category_display(key):
    """Readable category name: translated if fixed, formatted if user-created."""
    if key in MAP_CATEGORIES:
        return get_string(MAP_CATEGORIES[key])
    return key.replace('_', ' ').title()


def item_display(name):
    """Readable item name: translated if fixed, as-is if user-created."""
    return get_string(MAP_ITEMS[name]) if name in MAP_ITEMS else name


def _default_templates():
    return {
        'secciones_kodi': [
            {'name': get_string(30400), 'path': 'ActivateWindow(Videos,MovieTitles)', 'icon': 'DefaultMovies.png'},
            {'name': get_string(30417), 'path': 'ActivateWindow(Videos,TVShowTitles)', 'icon': 'DefaultTVShows.png'},
            {'name': get_string(30418), 'path': 'ActivateWindow(Home)', 'icon': 'DefaultHome.png'},
            {'name': get_string(30401), 'path': 'ActivateWindow(TVChannels)', 'icon': 'DefaultLiveTV.png'},
            {'name': get_string(30402), 'path': 'ActivateWindow(TVGuide)', 'icon': 'DefaultEPG.png'},
            {'name': get_string(30419), 'path': 'ActivateWindow(Radio)', 'icon': 'DefaultRadio.png'},
            {'name': get_string(30403), 'path': 'ActivateWindow(Music)', 'icon': 'DefaultMusic.png'},
            {'name': get_string(30420), 'path': 'ActivateWindow(Videos,MusicVideoTitles)', 'icon': 'DefaultMusicVideos.png'},
            {'name': get_string(30421), 'path': 'ActivateWindow(Weather)', 'icon': 'DefaultWeather.png'},
            {'name': get_string(30404), 'path': 'ActivateWindow(Pictures)', 'icon': 'DefaultPicture.png'},
            {'name': get_string(30422), 'path': 'ActivateWindow(Settings)', 'icon': 'DefaultIconSettings.png'},
            {'name': get_string(30423), 'path': 'ActivateWindow(FileManager)', 'icon': 'DefaultFile.png'},
            {'name': get_string(30424), 'path': 'ActivateWindow(SystemInfo)', 'icon': 'DefaultIconInfo.png'},
            {'name': get_string(30425), 'path': 'ActivateWindow(EventLog)', 'icon': 'DefaultIconInfo.png'},
            {'name': get_string(30426), 'path': 'ActivateWindow(AddonBrowser)', 'icon': 'DefaultAddon.png'},
        ],
        'comandos_sistema': [
            {'name': get_string(30405), 'path': 'ActivateWindow(ShutdownMenu)', 'icon': 'DefaultIconPower.png'},
            {'name': get_string(30406), 'path': 'UpdateLibrary(video)', 'icon': 'DefaultIconSync.png'},
            {'name': get_string(30407), 'path': 'CleanLibrary(video)', 'icon': 'DefaultAddon.png'},
            {'name': get_string(30427), 'path': 'ReloadSkin()', 'icon': 'DefaultIconRepeat.png'},
            {'name': get_string(30428), 'path': 'ActivateWindow(SkinSettings)', 'icon': 'DefaultAddon.png'},
            {'name': get_string(30423), 'path': 'ActivateWindow(FileManager)', 'icon': 'DefaultFile.png'},
        ],
    }


def _import_templates():
    file_path = xbmcgui.Dialog().browse(1, get_string(30250).replace('xml', 'json'), 'files', '.json')
    if not file_path:
        return
    try:
        with open(translatePath(file_path), 'r', encoding='utf-8') as f:
            imported = json.load(f)
        if not isinstance(imported, dict):
            raise ValueError(get_string(30394))

        merge_opts = [get_string(30317), get_string(30318), '« ' + get_string(30430)]
        merge_sel = xbmcgui.Dialog().select(get_string(30316), merge_opts)
        if merge_sel == 0:
            save_templates(imported)
            xbmcgui.Dialog().notification(get_string(30267), get_string(30313), xbmcgui.NOTIFICATION_INFO)
        elif merge_sel == 1:
            current = load_templates()
            for key, items in imported.items():
                if key in current:
                    existing_names = [i['name'] for i in current[key]]
                    current[key].extend(item for item in items if item['name'] not in existing_names)
                else:
                    current[key] = items
            save_templates(current)
            xbmcgui.Dialog().notification(get_string(30314), get_string(30315), xbmcgui.NOTIFICATION_INFO)
    # A syntactically valid JSON with the wrong shape (e.g. a category whose items are a number)
    # raises TypeError while iterating, which neither OSError nor ValueError covers; catch broadly
    # so a bad import file can't crash the templates editor, and surface the error to the user.
    except Exception as e:
        xbmcgui.Dialog().ok(get_string(30044), get_string(30264) + ":\n" + str(e))


def _export_templates():
    folder = xbmcgui.Dialog().browse(0, get_string(30218), 'files')
    if not folder:
        return
    default_name = 'favourites_templates_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    kb = xbmc.Keyboard(default_name, get_string(30319))
    kb.doModal()
    if not kb.isConfirmed() or not kb.getText():
        return
    file_path = os.path.join(translatePath(folder), kb.getText() + '.json')
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(load_templates(), f, ensure_ascii=False, indent=4)
        xbmcgui.Dialog().notification(get_string(30270), kb.getText(), xbmcgui.NOTIFICATION_INFO)
    except OSError as e:
        xbmcgui.Dialog().ok(get_string(30044), get_string(30271) + ":\n" + str(e))


def _edit_category(category):
    while True:
        templates = load_templates()
        items = templates.get(category, [])
        names = [item_display(i['name']) for i in items]
        names.append('[COLOR lime]+ ' + get_string(30294) + '[/COLOR]')
        names.append('[COLOR red]- ' + get_string(30295) + '[/COLOR]')
        names.append('[COLOR gray]« ' + get_string(30430) + '[/COLOR]')
        names.append(get_string(30520))

        item_sel = xbmcgui.Dialog().select(category_display(category), names)
        if item_sel == len(names) - 1:
            raise CloseAddon()
        if item_sel == -1 or item_sel == len(names) - 2:
            return

        if item_sel == len(names) - 3:  # Delete category
            if xbmcgui.Dialog().yesno(get_string(30167), get_string(30296).format(category_display(category))):
                del templates[category]
                save_templates(templates)
                xbmcgui.Dialog().notification(get_string(30297), category_display(category), xbmcgui.NOTIFICATION_INFO)
                return
            continue

        if item_sel == len(names) - 4:  # Add new item
            kb = xbmc.Keyboard('', get_string(30298))
            kb.doModal()
            if not kb.isConfirmed() or not kb.getText():
                continue
            new_name = kb.getText()
            kb = xbmc.Keyboard('', get_string(30299))
            kb.doModal()
            if not kb.isConfirmed() or not kb.getText():
                continue
            new_path = kb.getText()

            icon_sel = xbmcgui.Dialog().select(get_string(30300), [get_string(30301), get_string(30302)])
            if icon_sel == 0:
                kb = xbmc.Keyboard('DefaultAddon.png', get_string(30303))
                kb.doModal()
                new_icon = kb.getText() if kb.isConfirmed() and kb.getText() else 'DefaultAddon.png'
            elif icon_sel == 1:
                browse_result = xbmcgui.Dialog().browse(1, get_string(30185), 'pictures')
                new_icon = browse_result if browse_result else 'DefaultAddon.png'
            else:
                new_icon = 'DefaultAddon.png'

            items.append({'name': new_name, 'path': new_path, 'icon': new_icon})
            templates[category] = items
            save_templates(templates)
            xbmcgui.Dialog().notification(get_string(30293), new_name, xbmcgui.NOTIFICATION_INFO)
            continue

        # Edit existing item
        selected_item = items[item_sel]
        actions = [get_string(30304), get_string(30305), get_string(30306), get_string(30119), '« ' + get_string(30430)]
        action = xbmcgui.Dialog().select(selected_item['name'], actions)

        if action == 0:
            kb = xbmc.Keyboard(selected_item['name'], get_string(30307))
            kb.doModal()
            if kb.isConfirmed() and kb.getText():
                items[item_sel]['name'] = kb.getText()
                save_templates(templates)
        elif action == 1:
            kb = xbmc.Keyboard(selected_item['path'], get_string(30308))
            kb.doModal()
            if kb.isConfirmed() and kb.getText():
                items[item_sel]['path'] = kb.getText()
                save_templates(templates)
        elif action == 2:
            icon_sel = xbmcgui.Dialog().select(get_string(30300), [get_string(30301), get_string(30302)])
            new_icon = None
            if icon_sel == 0:
                kb = xbmc.Keyboard(selected_item['icon'], get_string(30309))
                kb.doModal()
                if kb.isConfirmed() and kb.getText():
                    new_icon = kb.getText()
            elif icon_sel == 1:
                browse_result = xbmcgui.Dialog().browse(1, get_string(30185), 'pictures')
                if browse_result:
                    new_icon = browse_result
            if new_icon:
                items[item_sel]['icon'] = new_icon
                save_templates(templates)
        elif action == 3:
            if xbmcgui.Dialog().yesno(get_string(30167), get_string(30168).format(selected_item['name'])):
                items.pop(item_sel)
                save_templates(templates)
                xbmcgui.Dialog().notification(get_string(30297), selected_item['name'], xbmcgui.NOTIFICATION_INFO)


def run_templates_editor():
    """Editor to customize template categories and items."""
    while True:
        templates = load_templates()
        category_keys = list(templates.keys())
        category_names = [
            f"{category_display(key)} ({get_string(30203).format(len(templates[key]))})"
            for key in category_keys
        ]
        opts = category_names + [
            '[COLOR lime]+ ' + get_string(30286) + '[/COLOR]',
            '[COLOR orange]' + get_string(30287) + '[/COLOR]',
            '[COLOR cyan]' + get_string(30288) + '[/COLOR]',
            '[COLOR cyan]' + get_string(30289) + '[/COLOR]',
            '[COLOR gray]« ' + get_string(30430) + '[/COLOR]',
            get_string(30520),
        ]
        sel = xbmcgui.Dialog().select(get_string(30285), opts)
        if sel == len(opts) - 1:
            raise CloseAddon()
        if sel == -1 or sel == len(opts) - 2:
            return

        if sel == len(opts) - 3:  # Import
            _import_templates()
        elif sel == len(opts) - 4:  # Export
            _export_templates()
        elif sel == len(opts) - 5:  # Reset
            if xbmcgui.Dialog().yesno(get_string(30167), get_string(30310)):
                save_templates(_default_templates())
                xbmcgui.Dialog().notification(get_string(30311), get_string(30312), xbmcgui.NOTIFICATION_INFO)
        elif sel == len(opts) - 6:  # New category
            kb = xbmc.Keyboard('', get_string(30290))
            kb.doModal()
            if kb.isConfirmed() and kb.getText():
                new_cat_name = kb.getText().strip()
                new_cat_key = new_cat_name.lower().replace(' ', '_')
                if new_cat_key in templates:
                    xbmcgui.Dialog().notification(get_string(30044), get_string(30292), xbmcgui.NOTIFICATION_WARNING)
                else:
                    templates[new_cat_key] = []
                    save_templates(templates)
                    xbmcgui.Dialog().notification(get_string(30293), new_cat_name, xbmcgui.NOTIFICATION_INFO)
        else:
            _edit_category(category_keys[sel])
