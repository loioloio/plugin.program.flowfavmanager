# -*- coding: utf-8 -*-
"""On-demand installer for the companion 'loiolog' addon (its repo + the addon itself).

Reached from the audit-log menu. Tries, in order: enable an already-present copy, install via the
Kodi repository, and finally a direct ZIP download as a fallback. Network and filesystem failures
are contained so a failed install never crashes the host addon.
"""
import json
import os
import re
import zipfile

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.common import get_string

EK_REPO_POLL_TIMEOUT_S  = 20
EK_ADDON_POLL_TIMEOUT_S = 60
EK_POLL_INTERVAL_MS     = 500
EK_MAX_REPO_ZIP_BYTES   = 50 * 1024 * 1024
EK_MAX_ADDON_ZIP_BYTES  = 200 * 1024 * 1024


class _InstallError(Exception):
    """Aborts the install flow; the message is surfaced to the user by the outer handler."""


def _zip_member_target(addons_dir, addons_dir_real, entry):
    """Resolve a ZIP entry under addons_dir, rejecting paths that escape it (Zip Slip)."""
    resolved = os.path.realpath(os.path.join(addons_dir, entry))
    if resolved != addons_dir_real and not resolved.startswith(addons_dir_real + os.sep):
        raise _InstallError(f'ZIP contains suspicious path: {entry}')
    return resolved


def _ek_addon_is_installed(addon_id):
    try:
        xbmcaddon.Addon(addon_id)
        return True
    except RuntimeError:
        return False


def _ek_addon_dir_present(addon_id):
    addon_dir = xbmcvfs.translatePath(f'special://home/addons/{addon_id}/')
    return os.path.isdir(addon_dir) and os.path.isfile(os.path.join(addon_dir, 'addon.xml'))


def _ek_jsonrpc(method, params=None):
    req = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        req['params'] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(req)))
    except ValueError:
        return {}


def _ek_addon_is_known(addon_id):
    resp = _ek_jsonrpc('Addons.GetAddonDetails', {'addonid': addon_id, 'properties': ['enabled']})
    return 'result' in resp and 'addon' in resp.get('result', {})


def _ek_enable_addon_jsonrpc(addon_id):
    resp = _ek_jsonrpc('Addons.SetAddonEnabled', {'addonid': addon_id, 'enabled': True})
    return resp.get('result') == 'OK'


def _ek_try_recover_addon(addon_id):
    if _ek_addon_is_known(addon_id):
        if _ek_enable_addon_jsonrpc(addon_id):
            xbmc.sleep(800)
            if _ek_addon_is_installed(addon_id):
                return True
    if not _ek_addon_dir_present(addon_id):
        return False
    xbmc.executebuiltin('UpdateLocalAddons()')
    xbmc.sleep(1500)
    if _ek_addon_is_known(addon_id) and _ek_enable_addon_jsonrpc(addon_id):
        xbmc.sleep(800)
        if _ek_addon_is_installed(addon_id):
            return True
    xbmc.executebuiltin(f'EnableAddon({addon_id})')
    xbmc.sleep(1500)
    return _ek_addon_is_installed(addon_id)


def _ek_get_repo_datadir(repo_id):
    repo_xml = xbmcvfs.translatePath(f'special://home/addons/{repo_id}/addon.xml')
    if not os.path.isfile(repo_xml):
        return None
    try:
        with open(repo_xml, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        xbmc.log(f'[loiolog_installer] Could not read datadir of repo {repo_id}: {e}', xbmc.LOGWARNING)
        return None
    m = re.search(r'<datadir[^>]*>\s*([^<\s]+)\s*</datadir>', content)
    if not m:
        return None
    url = m.group(1).strip()
    return url if url.endswith('/') else url + '/'


def _ek_find_addon_version_in_index(addons_xml_text, addon_id):
    aid = re.escape(addon_id)
    m = (re.search(r'<addon[^>]*\bid="' + aid + r'"[^>]*\bversion="([^"]+)"', addons_xml_text)
         or re.search(r'<addon[^>]*\bversion="([^"]+)"[^>]*\bid="' + aid + r'"', addons_xml_text))
    return m.group(1) if m else None


def _ek_install_addon_zip_directly(addon_id, repo_id, friendly_name):
    if not REQUESTS_AVAILABLE:
        return False
    zip_path = None
    try:
        datadir = _ek_get_repo_datadir(repo_id)
        if not datadir:
            xbmc.log(f'[loiolog_installer] Fallback ({friendly_name}): could not get datadir for {repo_id}', xbmc.LOGWARNING)
            return False
        try:
            r = requests.get(datadir + 'addons.xml', timeout=20)
            if r.status_code != 200:
                return False
            addons_xml = r.text
        except requests.RequestException:
            return False
        version = _ek_find_addon_version_in_index(addons_xml, addon_id)
        if not version:
            return False
        addon_zip_url = f'{datadir}{addon_id}/{addon_id}-{version}.zip'
        try:
            r = requests.get(addon_zip_url, timeout=60)
            if r.status_code != 200 or len(r.content) < 4 or r.content[:2] != b'PK':
                return False
            if len(r.content) > EK_MAX_ADDON_ZIP_BYTES:
                return False
        except requests.RequestException:
            return False
        addons_dir = xbmcvfs.translatePath('special://home/addons/')
        addons_dir_real = os.path.realpath(addons_dir)
        zip_path = os.path.join(xbmcvfs.translatePath('special://temp/'), f'{addon_id}-{version}.zip')
        with open(zip_path, 'wb') as f:
            f.write(r.content)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for entry in zf.namelist():
                _zip_member_target(addons_dir, addons_dir_real, entry)
            zf.extractall(addons_dir)
        try:
            os.remove(zip_path)
            zip_path = None
        except OSError:
            pass
        xbmc.executebuiltin('UpdateLocalAddons()')
        xbmc.sleep(1500)
        xbmc.executebuiltin(f'EnableAddon({addon_id})')
        xbmc.sleep(1500)
        for _ in range(20):
            if _ek_addon_is_installed(addon_id):
                return True
            xbmc.executebuiltin(f'EnableAddon({addon_id})')
            xbmc.sleep(EK_POLL_INTERVAL_MS)
        return _ek_try_recover_addon(addon_id) or _ek_addon_is_installed(addon_id)
    except Exception as e:
        # Broad on purpose: a multi-step download + extraction + Kodi-API install must never crash
        # the host addon. The specific failure is logged for diagnosis.
        xbmc.log(f'[loiolog_installer] Fallback exception: {e}', xbmc.LOGERROR)
        return False
    finally:
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass


def _ek_install_repo_and_addon(addon_id, repo_id, repo_zip_url, friendly_name):
    if _ek_addon_is_installed(addon_id):
        xbmcgui.Dialog().notification('Flow FavManager', get_string(30492).format(friendly_name), xbmcgui.NOTIFICATION_INFO, 3000)
        xbmc.executebuiltin('Container.Refresh')
        return True
    if _ek_try_recover_addon(addon_id):
        xbmcgui.Dialog().notification('Flow FavManager', get_string(30493).format(friendly_name), xbmcgui.NOTIFICATION_INFO, 3000)
        xbmc.executebuiltin('Container.Refresh')
        return True

    repo_already_present = _ek_addon_is_installed(repo_id)
    dp = xbmcgui.DialogProgress()
    dp.create(
        'Flow FavManager',
        get_string(30495).format(friendly_name) if not repo_already_present
        else get_string(30496).format(friendly_name)
    )
    zip_path = None
    bg = None
    try:
        if not repo_already_present:
            dp.update(5, get_string(30497))
            r = requests.get(repo_zip_url, timeout=30)
            if r.status_code != 200:
                raise _InstallError(f'HTTP Error {r.status_code}')
            if len(r.content) < 4 or r.content[:2] != b'PK':
                raise _InstallError('Downloaded file is not a valid ZIP')
            if len(r.content) > EK_MAX_REPO_ZIP_BYTES:
                raise _InstallError(f'Suspicious file size: {len(r.content) // 1024}KB')
            zip_path = os.path.join(xbmcvfs.translatePath('special://temp/'), f'{repo_id}.zip')
            with open(zip_path, 'wb') as f:
                f.write(r.content)
            dp.update(20, get_string(30498))
            addons_dir = xbmcvfs.translatePath('special://home/addons/')
            addons_dir_real = os.path.realpath(addons_dir)
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for entry in zf.namelist():
                    _zip_member_target(addons_dir, addons_dir_real, entry)
                zf.extractall(addons_dir)
            dp.update(35, get_string(30499))
            xbmc.executebuiltin('UpdateLocalAddons()')
            repo_iters = (EK_REPO_POLL_TIMEOUT_S * 1000) // EK_POLL_INTERVAL_MS
            repo_ready = False
            for i in range(repo_iters):
                if dp.iscanceled():
                    raise _InstallError('Cancelled by user')
                if _ek_addon_is_installed(repo_id):
                    repo_ready = True
                    break
                if i in (4, 12, 24):
                    xbmc.executebuiltin(f'EnableAddon({repo_id})')
                xbmc.sleep(EK_POLL_INTERVAL_MS)
            if not repo_ready:
                raise _InstallError(f'Kodi did not register the repository after {EK_REPO_POLL_TIMEOUT_S}s')
            dp.update(55, get_string(30500))
            xbmc.executebuiltin(f'EnableAddon({repo_id})')
            xbmc.sleep(1500)
        dp.update(65, get_string(30501))
        xbmc.executebuiltin('UpdateAddonRepos()')
        xbmc.sleep(3000)
        dp.close()
        xbmcgui.Dialog().notification('Flow FavManager', get_string(30502).format(friendly_name), xbmcgui.NOTIFICATION_INFO, 4000)
        xbmc.executebuiltin(f'InstallAddon({addon_id})')
        bg = xbmcgui.DialogProgressBG()
        bg.create('Flow FavManager', get_string(30503).format(friendly_name))
        addon_iters = (EK_ADDON_POLL_TIMEOUT_S * 1000) // EK_POLL_INTERVAL_MS
        addon_ready = False
        for i in range(addon_iters):
            if _ek_addon_is_installed(addon_id):
                addon_ready = True
                break
            xbmc.sleep(EK_POLL_INTERVAL_MS)
            pct = min(int((i + 1) * 100 / addon_iters), 99)
            bg.update(pct, 'Flow FavManager', get_string(30503).format(friendly_name))
        bg.close()
        bg = None
        if not addon_ready and _ek_try_recover_addon(addon_id):
            addon_ready = True
        if addon_ready:
            xbmcgui.Dialog().notification('Flow FavManager', get_string(30504).format(friendly_name), xbmcgui.NOTIFICATION_INFO, 4000)
            return True
        bg = xbmcgui.DialogProgressBG()
        bg.create('Flow FavManager', get_string(30505).format(friendly_name))
        bg.update(50)
        ok = _ek_install_addon_zip_directly(addon_id, repo_id, friendly_name)
        bg.close()
        bg = None
        if ok:
            xbmcgui.Dialog().notification('Flow FavManager', get_string(30504).format(friendly_name), xbmcgui.NOTIFICATION_INFO, 4000)
            return True
        xbmcgui.Dialog().ok('Flow FavManager', get_string(30506).format(friendly_name))
        return False
    except Exception as e:
        # Broad on purpose: the whole repo+addon install is a multi-step network/UI operation that
        # must never crash the host addon. The specific failure is logged and shown to the user.
        try:  # closing the progress dialogs is best-effort UI cleanup
            dp.close()
            if bg:
                bg.close()
        except RuntimeError:
            pass
        xbmc.log(f'[loiolog_installer] Error installing {friendly_name}: {e}', xbmc.LOGERROR)
        xbmcgui.Dialog().ok(get_string(30507), get_string(30508).format(friendly_name, e))
        return False
    finally:
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass


def launch_loiolog():
    if not REQUESTS_AVAILABLE:
        xbmcgui.Dialog().ok('Flow FavManager', get_string(30494))
        return False

    addon_id = 'plugin.program.loiolog'
    source = 'https://loioloio.github.io/loiolog/'
    if _ek_addon_is_installed(addon_id):
        xbmcgui.Dialog().notification('Flow FavManager', get_string(30510), xbmcgui.NOTIFICATION_INFO, 2000)
        xbmc.executebuiltin(f'RunAddon({addon_id})')
        return True

    idx = xbmcgui.Dialog().select(get_string(30511), [get_string(30512), get_string(30513)])
    if idx == 0:
        if xbmcgui.Dialog().yesno('Flow FavManager', get_string(30514)):
            ok = _ek_install_repo_and_addon(
                addon_id, 'repository.loiolog',
                'https://raw.githubusercontent.com/loioloio/loiolog/main/repository.loiolog-1.0.0.zip',
                'loiolog')
            if ok:
                xbmc.executebuiltin(f'RunAddon({addon_id})')
                return True
    elif idx == 1:
        xbmcgui.Dialog().textviewer(get_string(30515), get_string(30516).format(source))
    return False
