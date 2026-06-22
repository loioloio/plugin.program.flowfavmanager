# -*- coding: utf-8 -*-
"""Plugin dispatcher and main menu construction.

Heavy modules (editor, menus) are imported inside the branch that uses them, not at the top;
an invocation that only paints the menu or a widget does not load the ~1300-line editor.
"""
import os
import re
import shutil
import traceback
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin

from resources.lib.common import ADDON, AUDIT_FILE, BASE_URL, CloseAddon, PATHS, PLUGIN_ID, get_string, log_debug, translatePath

_WIN_PROP = 'flowfavmanager.web_remote.port'


def _get_local_ip():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))  # no real connection; just picks the outgoing interface
            return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'


def _open_in_browser(url):
    if xbmc.getCondVisibility('System.Platform.Android'):
        xbmc.executebuiltin(f'StartAndroidActivity("","android.intent.action.VIEW","","{url}")')
        return True
    import webbrowser
    try:
        return webbrowser.open(url, new=2)
    except Exception:
        return False


def _wait_for_port(win):
    # The plugin requests start via a setting; the service brings up the server and publishes
    # the port in the window property. We wait up to 3 s for it to appear.
    for _ in range(6):
        xbmc.sleep(500)
        port_str = win.getProperty(_WIN_PROP)
        if port_str:
            return port_str
    return ''


def _web_remote_menu():
    win = xbmcgui.Window(10000)
    dlg = xbmcgui.Dialog()
    port_str = win.getProperty(_WIN_PROP)

    if not port_str:
        if not dlg.yesno(get_string(30472), get_string(30475),
                         nolabel=get_string(30120), yeslabel=get_string(30476)):
            return
        ADDON.setSetting('web_remote_enabled', 'true')
        port_str = _wait_for_port(win)
        if not port_str:
            dlg.notification(get_string(30472), get_string(30477),
                             xbmcgui.NOTIFICATION_WARNING, 4000)
            return

    ip = _get_local_ip()
    url = f'http://{ip}:{port_str}'
    opts = [
        f'[COLOR lime]●[/COLOR] [B]{url}[/B]',
        get_string(30478),
        get_string(30479),
        '[COLOR gray]« ' + get_string(30430) + '[/COLOR]',
    ]
    sel = dlg.select(get_string(30472), opts)
    if sel == 1:
        if not _open_in_browser(url):
            dlg.ok(get_string(30472), get_string(30480).format(url))
    elif sel == 2:
        ADDON.setSetting('web_remote_enabled', 'false')
        dlg.notification(get_string(30472), get_string(30481),
                         xbmcgui.NOTIFICATION_INFO, 2000)


def show_about_dialog():
    msg = (
        "[B]Flow FavManager[/B]\n"
        "[COLOR gray]-----------------------------------------------[/COLOR]\n"
        + get_string(30341) + "\n"
        "https://loioloio.github.io/flowfavweb/"
    )
    xbmcgui.Dialog().ok(get_string(30340), msg)


def _query_arg(param, key):
    args = urllib.parse.parse_qs(urllib.parse.urlparse(param).query)
    return args.get(key, [''])[0]


def _open_editor():
    from resources.lib.editor import FavouritesEditor
    from resources.lib.database import FavouritesEngine

    # Guard before building the window: a corrupt/0-byte favourites.xml would load as an empty
    # list, and the first Save would overwrite the file and wipe every favourite. Fail fast.
    if not FavouritesEngine().load() and os.path.exists(PATHS['favourites']):
        xbmcgui.Dialog().ok(get_string(30387), get_string(30523))
        return

    view_setting = ADDON.getSetting('view_mode') or '0'
    thumb_size = ADDON.getSetting('icon_scale') or '0'
    font_size = ADDON.getSetting('text_scale') or '1'

    if view_setting == '1':  # Grid
        prop_mode = '1' if thumb_size == '0' else '0'  # small / large
    else:  # List: large if the font or thumb grow, compact otherwise
        prop_mode = '3' if (thumb_size == '1' or font_size != '0') else '2'

    gui = FavouritesEditor('editor.xml', PATHS['addon_path'], 'Default', '1080i')
    gui.setProperty('view_mode', prop_mode)
    gui.setProperty('addonIcon', os.path.join(PATHS['addon_path'], 'icon.png'))
    gui.doModal()
    reopen = getattr(gui, 'reopen_main', False)
    del gui
    if reopen:
        # Open the addon's main menu once the modal is fully gone, so closing the editor and
        # navigating don't race for focus. ActivateWindow(Programs,...,return) is the documented
        # way to open a program plugin's listing and keeps Back returning to the previous window.
        addon_id = ADDON.getAddonInfo('id')
        xbmc.executebuiltin(f'ActivateWindow(Programs,plugin://{addon_id}/,return)')


def _clear_texture_cache():
    """Clear the texture cache to force Kodi to reload icons. Fault-tolerant."""
    db_dir = translatePath('special://profile/Database/')
    thumbs_path = translatePath('special://profile/Thumbnails/')
    # The textures DB carries a version suffix that bumps across major Kodi releases
    # (Textures13.db on Kodi 18-21, higher later), so match by prefix instead of hardcoding.
    try:
        for name in os.listdir(db_dir):
            if name.startswith('Textures') and name.endswith('.db'):
                try:
                    os.remove(os.path.join(db_dir, name))
                except OSError:
                    pass  # usually locked on Windows; continue with the images
    except OSError:
        pass
    try:
        if os.path.exists(thumbs_path):
            shutil.rmtree(thumbs_path)
            xbmc.sleep(200)
            os.mkdir(thumbs_path)
    except OSError:
        pass  # some file is locked; not critical


def _save_reload():
    if xbmcgui.Dialog().yesno(get_string(30351), get_string(30377)):
        _clear_texture_cache()
        xbmcgui.Dialog().notification(get_string(30351), get_string(30352), xbmcgui.NOTIFICATION_INFO, 2000)
        xbmc.sleep(1000)
    xbmc.executebuiltin(f"LoadProfile({xbmc.getInfoLabel('System.ProfileName')})")


def _add_editor_to_kodi_favourites():
    from resources.lib.database import FavouriteEntry, FavouritesEngine

    addon_id = ADDON.getAddonInfo('id')
    editor_url = f'RunPlugin(plugin://{addon_id}/dialog)'
    editor_name = f'{ADDON.getAddonInfo("name")} - {get_string(30326)}'
    editor_icon = ADDON.getAddonInfo('icon')

    engine = FavouritesEngine()
    engine.load()

    for entry in engine.entries:
        if entry.url == editor_url:
            xbmcgui.Dialog().notification(
                ADDON.getAddonInfo('name'), get_string(30519),
                xbmcgui.NOTIFICATION_INFO, 2500
            )
            return

    engine.entries.append(FavouriteEntry(editor_name, editor_icon, editor_url))
    if engine.save():
        xbmcgui.Dialog().notification(
            ADDON.getAddonInfo('name'), get_string(30518),
            xbmcgui.NOTIFICATION_INFO, 2000
        )
    else:
        xbmcgui.Dialog().notification(
            get_string(30044), get_string(30375),
            xbmcgui.NOTIFICATION_ERROR, 2500
        )


def _editor_settings_menu():
    _VIEW  = [30002, 30003]
    _ICON  = [30005, 30006]
    _TEXT  = [30008, 30009, 30006]
    _BG    = [30012, 30013, 30014, 30015]
    _SEL   = [30017, 30018, 30019, 30020, 30021, 30022]
    _MULTI = [30024, 30025, 30019, 30026]

    def _cur(sid, ids, fallback='0'):
        v = ADDON.getSetting(sid) or fallback
        try:
            return get_string(ids[int(v)])
        except (IndexError, ValueError):
            return get_string(ids[0])

    def _pick(title_id, ids, sid, fallback='0'):
        cur = int(ADDON.getSetting(sid) or fallback)
        sel = xbmcgui.Dialog().select(
            get_string(title_id),
            [get_string(s) for s in ids],
            preselect=cur
        )
        if sel >= 0:
            ADDON.setSetting(sid, str(sel))

    while True:
        blind = ADDON.getSetting('colorblindMode') == 'true'
        opts = [
            f'{get_string(30001)}: [COLOR gray]{_cur("view_mode", _VIEW)}[/COLOR]',
            f'{get_string(30004)}: [COLOR gray]{_cur("icon_scale", _ICON)}[/COLOR]',
            f'{get_string(30007)}: [COLOR gray]{_cur("text_scale", _TEXT, "1")}[/COLOR]',
            f'{get_string(30011)}: [COLOR gray]{_cur("colorBackground", _BG)}[/COLOR]',
            f'{get_string(30016)}: [COLOR gray]{_cur("colorSelection", _SEL)}[/COLOR]',
            f'{get_string(30023)}: [COLOR gray]{_cur("colorMultiselect", _MULTI)}[/COLOR]',
            f'{get_string(30027)}: [COLOR {"lime" if blind else "gray"}]{"ON" if blind else "OFF"}[/COLOR]',
            '« ' + get_string(30430),
            get_string(30520),
        ]
        sel = xbmcgui.Dialog().select(get_string(30326), opts)
        if sel == 0:
            _pick(30001, _VIEW, 'view_mode')
        elif sel == 1:
            _pick(30004, _ICON, 'icon_scale')
        elif sel == 2:
            _pick(30007, _TEXT, 'text_scale', '1')
        elif sel == 3:
            _pick(30011, _BG, 'colorBackground')
        elif sel == 4:
            _pick(30016, _SEL, 'colorSelection')
        elif sel == 5:
            _pick(30023, _MULTI, 'colorMultiselect')
        elif sel == 6:
            ADDON.setSetting('colorblindMode', 'false' if blind else 'true')
        elif sel == len(opts) - 1:
            raise CloseAddon()
        else:
            return


def _settings_menu():
    from resources.lib import security, templates
    while True:
        audit_enabled = ADDON.getSetting('enable_audit_log') == 'true'
        opts = [
            get_string(30326), get_string(30285), get_string(30058),
            get_string(30395) if audit_enabled else get_string(30396),
            get_string(30028),
            get_string(30517),
            '« ' + get_string(30430),
            get_string(30520),
        ]
        sel = xbmcgui.Dialog().select(get_string(30422), opts)
        if sel == 0:
            _editor_settings_menu()
        elif sel == 1:
            templates.run_templates_editor()
        elif sel == 2:
            security.run_security_menu()
        elif sel == 3:
            _audit_log_menu()
        elif sel == 4:
            ADDON.openSettings()
        elif sel == 5:
            _add_editor_to_kodi_favourites()
        elif sel == len(opts) - 1:
            raise CloseAddon()
        else:
            return


def _audit_log_menu():
    while True:
        audit_on = ADDON.getSetting('enable_audit_log') == 'true'
        status_str = get_string(30413) if audit_on else get_string(30414)
        color_str = 'lime' if audit_on else 'gray'
        opts = [
            get_string(30412).format(color=color_str, status=status_str),
            get_string(30415) if audit_on else get_string(30416),
            get_string(30397), get_string(30378),
            get_string(30491),
            "[COLOR gray]« " + get_string(30430) + "[/COLOR]",
            get_string(30520),
        ]
        sel = xbmcgui.Dialog().select(get_string(30386), opts)
        if sel == len(opts) - 1:
            raise CloseAddon()
        elif sel == 1:  # Toggle
            new_val = 'false' if audit_on else 'true'
            ADDON.setSetting('enable_audit_log', new_val)
            xbmcgui.Dialog().notification(get_string(30353), get_string(30354) if new_val == 'true' else get_string(30355), xbmcgui.NOTIFICATION_INFO)
        elif sel == 2:  # View
            if os.path.exists(AUDIT_FILE):
                try:
                    with open(AUDIT_FILE, 'r', encoding='utf-8') as f:
                        content = f.read()
                except OSError as e:
                    xbmcgui.Dialog().ok(get_string(30044), get_string(30374) + f"\n{e}")
                    continue
                if content.strip():
                    xbmcgui.Dialog().textviewer(get_string(30386), content)
                else:
                    xbmcgui.Dialog().ok(get_string(30370), get_string(30371))
            else:
                xbmcgui.Dialog().ok(get_string(30372), get_string(30373))
        elif sel == 3:  # Delete
            if os.path.exists(AUDIT_FILE):
                if xbmcgui.Dialog().yesno(get_string(30378), get_string(30379)):
                    try:
                        os.remove(AUDIT_FILE)
                        xbmcgui.Dialog().notification(get_string(30353), get_string(30356), xbmcgui.NOTIFICATION_INFO)
                    except OSError as e:
                        log_debug(f'Could not delete audit log: {e}')
            else:
                xbmcgui.Dialog().notification(get_string(30357), get_string(30358), xbmcgui.NOTIFICATION_INFO)
        elif sel == 4:  # loiolog
            from resources.lib.loiolog_installer import launch_loiolog
            if launch_loiolog():
                return  # launched successfully: close this menu so loiolog gets focus
        else:
            return


def _open_kodi_favourites():
    # Kodi 21+ uses FavouritesBrowser; earlier versions use Favourites.
    version = xbmc.getInfoLabel('System.BuildVersion')
    major = 19
    try:
        if version:
            major = int(version.split('.')[0])
    except ValueError:
        pass
    window = 'FavouritesBrowser' if major >= 21 else 'Favourites'
    xbmc.executebuiltin(f'ActivateWindow({window})')


def _list_profile_contents(param):
    from resources.lib.database import load_profile
    from resources.lib.listing import build_list_item

    filename = _query_arg(param, 'file')
    if not filename:
        log_debug(f'explore_profile without filename: {param}')
        xbmcplugin.endOfDirectory(PLUGIN_ID, False)
        return
    try:
        entries = load_profile(filename)
    except (OSError, ValueError) as e:
        log_debug(f'Error exploring profile: {e}')
        xbmcgui.Dialog().notification(get_string(30044), get_string(30359), xbmcgui.NOTIFICATION_ERROR)
        xbmcplugin.endOfDirectory(PLUGIN_ID, False)
        return

    if not entries:
        xbmcplugin.addDirectoryItem(PLUGIN_ID, '', xbmcgui.ListItem(label=get_string(30398)), False)
    for entry in entries:
        li, target_url, is_folder = build_list_item(entry)
        xbmcplugin.addDirectoryItem(PLUGIN_ID, target_url, li, is_folder)
    xbmcplugin.endOfDirectory(PLUGIN_ID)


def _search_profiles(param):
    from resources.lib.database import FavouriteEntry, get_profiles
    from resources.lib.listing import build_list_item

    query = _query_arg(param, 'q')
    if not query:
        kb = xbmc.Keyboard('', get_string(30320))
        kb.doModal()
        if not kb.isConfirmed() or not kb.getText():
            xbmcplugin.endOfDirectory(PLUGIN_ID, False)
            return
        query = kb.getText()

    query_lower = query.lower()
    results = []
    for p in get_profiles():
        for entry_data in p.get('entries', []):
            clean_name = re.sub(r'\[COLOR[^\]]*\]|\[/COLOR\]|\[B\]|\[/B\]|\[I\]|\[/I\]', '', entry_data.get('name', ''))
            if query_lower in clean_name.lower():
                results.append({**entry_data, 'profile': p['name']})

    xbmcplugin.setContent(PLUGIN_ID, 'files')
    if not results:
        li = xbmcgui.ListItem(label=f"[COLOR gray]{get_string(30321).format(query)}[/COLOR]")
        li.setInfo('video', {'plot': get_string(30322)})
        xbmcplugin.addDirectoryItem(PLUGIN_ID, '', li, False)
    else:
        for r in results:
            temp_entry = FavouriteEntry(r.get('name', ''), r.get('thumb', ''), r.get('url', ''))
            li, target_url, is_folder = build_list_item(temp_entry)
            li.setLabel(f"{r.get('name', '')} [COLOR gray]({r['profile']})[/COLOR]")
            li.setInfo('video', {'plot': f"{get_string(30323)} {r['profile']}\nURL: {r.get('url', '')[:80]}..."})
            xbmcplugin.addDirectoryItem(PLUGIN_ID, target_url, li, is_folder)
    xbmcplugin.endOfDirectory(PLUGIN_ID)


def _delete_profile_action(param):
    from resources.lib.database import delete_profile
    filename = _query_arg(param, 'file')
    if filename and xbmcgui.Dialog().yesno(get_string(30380), get_string(30381)):
        if delete_profile(filename):
            xbmcgui.Dialog().notification(get_string(30360), get_string(30361), xbmcgui.NOTIFICATION_INFO)
            xbmc.executebuiltin('Container.Refresh')
        else:
            xbmcgui.Dialog().notification(get_string(30044), get_string(30362), xbmcgui.NOTIFICATION_ERROR)


def _rename_profile_action(param):
    from resources.lib.database import delete_profile, load_profile, save_profile
    filename = _query_arg(param, 'file')
    current_name = _query_arg(param, 'name')
    if not filename:
        return
    kb = xbmc.Keyboard(current_name, get_string(30382))
    kb.doModal()
    if not kb.isConfirmed() or not kb.getText():
        return
    try:
        entries = load_profile(filename)
        new_filename = save_profile(kb.getText(), entries)
        # Same-name-after-sanitization renames map to the same file; deleting it would wipe
        # the profile save_profile just overwrote, so only delete when the file truly differs.
        if new_filename != filename:
            delete_profile(filename)
        xbmcgui.Dialog().notification(get_string(30363), get_string(30364), xbmcgui.NOTIFICATION_INFO)
        xbmc.executebuiltin('Container.Refresh')
    except (OSError, ValueError) as e:
        xbmcgui.Dialog().notification(get_string(30044), str(e), xbmcgui.NOTIFICATION_ERROR)


def _widget(param):
    from resources.lib.database import get_profiles, load_profile
    from resources.lib.listing import build_list_item

    profile_val = _query_arg(param, 'profile') or _query_arg(param, 'file')
    if not profile_val:
        # No parameter: list profiles as widget folders.
        for p in get_profiles():
            url = BASE_URL + 'widget?profile=' + urllib.parse.quote(p['filename'])
            li = xbmcgui.ListItem(label=p['name'])
            li.setArt({'thumb': 'DefaultFolder.png', 'icon': 'DefaultFolder.png'})
            xbmcplugin.addDirectoryItem(PLUGIN_ID, url, li, True)
        xbmcplugin.endOfDirectory(PLUGIN_ID)
        return

    entries = load_profile(profile_val)
    if not entries and not profile_val.endswith('.json'):
        for p in get_profiles():
            if p['name'] == profile_val:
                entries = load_profile(p['filename'])
                break

    xbmcplugin.setContent(PLUGIN_ID, 'files')
    if not entries:
        xbmcplugin.addDirectoryItem(PLUGIN_ID, '', xbmcgui.ListItem(label=get_string(30399)), False)
    else:
        for entry in entries:
            li, target_url, is_folder = build_list_item(entry)
            xbmcplugin.addDirectoryItem(PLUGIN_ID, target_url, li, is_folder)
    xbmcplugin.endOfDirectory(PLUGIN_ID)


def _explore():
    from resources.lib.database import get_profiles
    xbmcplugin.setContent(PLUGIN_ID, 'files')

    shortcuts = [
        ("[COLOR violet][B]" + get_string(30342) + "[/B][/COLOR]", 'DefaultAddonService.png', get_string(30343), 'profiles', False),
        ("[COLOR cyan][B]" + get_string(30320) + "[/B][/COLOR]", 'DefaultAddonsSearch.png', get_string(30322), 'search_profiles', True),
        ("[COLOR yellow][B]" + get_string(30344) + "[/B][/COLOR]", 'DefaultPlaylist.png', get_string(30345), 'widget', True),
    ]
    for label, icon, plot, route, is_folder in shortcuts:
        li = xbmcgui.ListItem(label=label)
        li.setArt({'icon': icon, 'thumb': icon})
        li.setInfo('video', {'plot': plot})
        xbmcplugin.addDirectoryItem(PLUGIN_ID, BASE_URL + route, li, is_folder)

    for p in get_profiles():
        url = BASE_URL + 'explore_profile?file=' + urllib.parse.quote(p['filename'])
        li = xbmcgui.ListItem(label=p['name'])
        li.setArt({'icon': 'DefaultUser.png'})
        li.setInfo('video', {'plot': f"{len(p['entries'])} items.\nModified: {p['date']}"})
        cmd_delete = f"RunPlugin({BASE_URL}delete_profile?file={urllib.parse.quote(p['filename'])})"
        cmd_rename = f"RunPlugin({BASE_URL}rename_profile?file={urllib.parse.quote(p['filename'])}&name={urllib.parse.quote(p['name'])})"
        li.addContextMenuItems([(get_string(30279), cmd_rename), (get_string(30283), cmd_delete)])
        xbmcplugin.addDirectoryItem(PLUGIN_ID, url, li, True)
    xbmcplugin.endOfDirectory(PLUGIN_ID)


def _maybe_first_run():
    """On first launch, offer to add the advanced editor to Kodi's favourites.

    The flag is written before prompting, so the dialog shows only once even if something fails
    afterwards. Shares its directory with the addon's other flags (view_wl_init).
    """
    flag = os.path.join(os.path.dirname(PATHS['profiles']), 'first_run_done')
    if os.path.exists(flag):
        return
    try:
        with open(flag, 'w') as f:
            f.write('1')
    except OSError as e:
        log_debug(f'Could not write first-run flag: {e}')
    if xbmcgui.Dialog().yesno(ADDON.getAddonInfo('name'), get_string(30521)):
        _add_editor_to_kodi_favourites()


def _main_menu():
    # Empty content (not 'files'). This way the generic templates draw ListItem.Icon on each
    # row; with 'files', Estuary's "List" view only draws the status overlay, not the icon.
    xbmcplugin.setContent(PLUGIN_ID, '')

    def media(name):
        return os.path.join(PATHS['media'], name)

    def add_item(label, route, icon_name, desc, context_items=None, is_folder=False):
        desc_styled = f'[COLOR white][B]{desc}[/B][/COLOR]'
        li = xbmcgui.ListItem(label=f'[COLOR white][B]{label}[/B][/COLOR]')
        li.setArt({'thumb': icon_name, 'icon': icon_name})
        li.setInfo('video', {'title': label, 'plot': desc_styled, 'plotoutline': desc_styled})
        if context_items:
            li.addContextMenuItems(context_items)
        xbmcplugin.addDirectoryItem(PLUGIN_ID, BASE_URL + route, li, is_folder)

    add_item(get_string(30324), 'explore', media('ff_profiles.png'), get_string(30325), is_folder=True)
    add_item(get_string(30326), 'dialog', media('ff_adv_editor.png'), get_string(30327))
    add_item(get_string(30328), 'simple_editor', media('ff_quick_editor.png'), get_string(30329))
    add_item(get_string(30216), 'backup_menu', media('ff_backup.png'), get_string(30457))
    add_item(get_string(30330), 'save_reload', media('ff_reload.png'), get_string(30331))
    add_item(get_string(30459), 'autostart_menu', media('ff_autostart.png'), get_string(30460))
    add_item(get_string(30472), 'web_remote', media('ff_webremote.png'), get_string(30474))
    add_item(get_string(30334), 'open_favourites', media('ff_favourites.png'), get_string(30335))

    is_ee = ADDON.getSetting('easter_egg') == 'true'
    lbl_about = get_string(30429) + ADDON.getAddonInfo('version') if is_ee else get_string(30338)
    desc_about = get_string(30341) if is_ee else get_string(30339)
    ctx_ee = [(get_string(30451), f'RunPlugin({BASE_URL}toggle_ee)')]
    add_item(lbl_about, 'about', media('ff_about.png'), desc_about, context_items=ctx_ee)

    add_item(get_string(30332), 'settings', media('ff_config.png'), get_string(30333))
    add_item(get_string(30336), 'exit_only', media('ff_exit.png'), get_string(30337))
    xbmcplugin.endOfDirectory(PLUGIN_ID)

    # Estuary opens program addons in the "List" view (id 50), which does not draw the item's
    # icon. We force "Wide List" (id 55) only the first time; afterwards Kodi remembers what the
    # user picks. Without sleep(250) the SetViewMode runs before the container loads and Kodi
    # overrides it with the remembered view.
    if xbmc.getSkinDir().startswith('skin.estuary'):
        flag = os.path.join(os.path.dirname(PATHS['profiles']), 'view_wl_init')
        if not os.path.exists(flag):
            xbmc.sleep(250)
            xbmc.executebuiltin('Container.SetViewMode(55)')
            try:
                with open(flag, 'w') as f:
                    f.write('1')
            except OSError:
                pass

    _maybe_first_run()


def route(param):
    try:
        if '/profiles' in param:
            from resources.lib.profiles import run_profiles_menu
            run_profiles_menu()
        elif '/dialog' in param:
            _open_editor()
        elif '/simple_editor' in param:
            from resources.lib.simple_editor import run_simple_editor
            run_simple_editor()
        elif '/backup_menu' in param:
            from resources.lib.backup import run_backup_menu
            run_backup_menu()
        elif '/autostart_menu' in param:
            from resources.lib.autostart import run_autostart_menu
            run_autostart_menu()
        elif '/web_remote' in param:
            _web_remote_menu()
        elif '/save_reload' in param:
            _save_reload()
        elif '/settings' in param:
            _settings_menu()
        elif '/templates_editor' in param:
            from resources.lib.templates import run_templates_editor
            run_templates_editor()
        elif '/about' in param:
            show_about_dialog()
        elif '/open_favourites' in param:
            _open_kodi_favourites()
        elif '/execute' in param:
            cmd = _query_arg(param, 'cmd')
            if cmd:
                log_debug(f'Executing command: {cmd}')
                xbmc.executebuiltin(cmd)
        elif '/explore_profile' in param:
            _list_profile_contents(param)
        elif '/search_profiles' in param:
            _search_profiles(param)
        elif '/delete_profile' in param:
            _delete_profile_action(param)
        elif '/rename_profile' in param:
            _rename_profile_action(param)
        elif '/widget' in param:
            _widget(param)
        elif '/explore' in param:
            _explore()
        elif '/toggle_ee' in param:
            current = ADDON.getSetting('easter_egg') == 'true'
            ADDON.setSetting('easter_egg', 'false' if current else 'true')
            xbmc.executebuiltin('Container.Refresh')
        elif '/exit_only' in param:
            xbmc.executebuiltin('Action(Back)')
        else:
            _main_menu()
    except CloseAddon:
        pass  # Dialogs already dismissed; plugin ends and Kodi shows the main listing again.
    except Exception as e:
        tb = traceback.format_exc()
        log_debug('CRASH: ' + tb)
        xbmcgui.Dialog().textviewer(get_string(30387), str(e) + "\n\n" + tb)
