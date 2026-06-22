# -*- coding: utf-8 -*-
"""PIN protection for the addon: startup gate, recovery and configuration menu."""
import json
import os

import xbmc
import xbmcgui

from resources.lib.common import ADDON_ID, CloseAddon, get_string, log_audit, log_debug, translatePath

SECURITY_FILE = translatePath(f'special://profile/addon_data/{ADDON_ID}/security.json')
RESET_FILE = translatePath(f'special://profile/addon_data/{ADDON_ID}/reset_pass.txt')
SESSION_UNLOCKED_PROP = 'FlowFavManager_Unlocked'


def load_security_config():
    try:
        if os.path.exists(SECURITY_FILE):
            with open(SECURITY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (OSError, ValueError) as e:
        # A corrupt file is treated as "unprotected" (fail-open) so the owner is not locked
        # out; the emergency reset file is the other escape hatch.
        log_debug(f'security.json unreadable, ignoring protection: {e}')
    return {'enabled': False, 'pin': '', 'question': '', 'answer': ''}


def save_security_config(config):
    try:
        os.makedirs(os.path.dirname(SECURITY_FILE), exist_ok=True)
        with open(SECURITY_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except OSError as e:
        log_debug(f'Error saving security config: {e}')
        return False


def check_reset_file():
    """Emergency reset: if the file exists, disable protection and delete it."""
    if not os.path.exists(RESET_FILE):
        return False
    try:
        os.remove(RESET_FILE)
    except OSError:
        return False
    config = load_security_config()
    config['enabled'] = False
    config['pin'] = ''
    save_security_config(config)
    xbmcgui.Dialog().ok(get_string(30226), get_string(30227))
    log_audit('AUTH_RESET_FILE', 'Security reset via emergency file')
    return True


def is_session_unlocked():
    # Window(10000) = Home, persists for the whole Kodi session.
    return xbmcgui.Window(10000).getProperty(SESSION_UNLOCKED_PROP) == 'true'


def set_session_unlocked():
    xbmcgui.Window(10000).setProperty(SESSION_UNLOCKED_PROP, 'true')


def check_security_gate():
    """Ask for the PIN when protection is active. Returns True if access can continue."""
    if check_reset_file():
        return True

    config = load_security_config()
    if not config.get('enabled', False):
        return True
    if is_session_unlocked():
        return True

    correct_pin = config.get('pin', '')
    attempts = 3
    while attempts > 0:
        kb = xbmc.Keyboard('', get_string(30048).format(attempts))
        kb.setHiddenInput(True)
        kb.doModal()
        if not kb.isConfirmed():
            return False
        if kb.getText() == correct_pin:
            set_session_unlocked()
            return True
        attempts -= 1
        # Audit every wrong attempt, including the last one (attempts == 0) that triggers the
        # lockout; keeping it inside the `if` below left a gap right at the security-relevant event.
        log_audit('AUTH_FAIL_PIN', f'Wrong PIN. Attempts left: {attempts}')
        if attempts > 0:
            xbmcgui.Dialog().notification(get_string(30047), get_string(30048).format(attempts), xbmcgui.NOTIFICATION_WARNING)

    # Out of attempts; offer recovery via the security question.
    recovery_opts = [get_string(30050), get_string(30051)]
    sel = xbmcgui.Dialog().select(get_string(30049), recovery_opts)
    if sel != 0:
        return False

    question = config.get('question', get_string(30052))
    answer_correct = config.get('answer', '').lower().strip()
    if not answer_correct:
        xbmcgui.Dialog().ok(get_string(30044), get_string(30053))
        return False

    kb = xbmc.Keyboard('', question)
    kb.doModal()
    if kb.isConfirmed() and kb.getText().lower().strip() == answer_correct:
        # Only unlocks this session; protection stays active on the next launch.
        xbmcgui.Dialog().ok(get_string(30054), get_string(30055))
        set_session_unlocked()
        log_audit('AUTH_RECOVERED_QUESTION', 'Access recovered via security question')
        return True

    xbmcgui.Dialog().ok(get_string(30056), get_string(30057) + os.path.dirname(SECURITY_FILE))
    log_audit('AUTH_FAIL_QUESTION', 'Wrong answer to security question')
    return False


def run_security_menu():
    """Menu to configure protection (PIN, recovery question, enable/disable)."""
    while True:
        config = load_security_config()
        is_enabled = config.get('enabled', False)
        has_pin = bool(config.get('pin', ''))
        has_question = bool(config.get('answer', ''))

        status = f"[COLOR lime]{get_string(30059)}[/COLOR]" if is_enabled else f"[COLOR gray]{get_string(30060)}[/COLOR]"
        opts = [
            get_string(30061).format(status),
            get_string(30062) if has_pin else get_string(30063),
            get_string(30064),
            get_string(30065) if not is_enabled else get_string(30066),
            f"[COLOR gray]{get_string(30067)}[/COLOR]",
            get_string(30520),
        ]
        sel = xbmcgui.Dialog().select(get_string(30058), opts)
        if sel == -1 or sel == 4:
            return
        if sel == len(opts) - 1:
            raise CloseAddon()

        if sel == 0:
            info_msg = get_string(30069)
            if is_enabled:
                info_msg += f"{get_string(30061).format(get_string(30059))}\n"
                info_msg += get_string(30070).format(get_string(30071) if has_question else get_string(30072)) + "\n\n"
                info_msg += get_string(30073)
                info_msg += os.path.dirname(SECURITY_FILE)
            else:
                info_msg += get_string(30061).format(get_string(30060))
            xbmcgui.Dialog().textviewer(get_string(30068), info_msg)

        elif sel == 1:
            kb = xbmc.Keyboard('', get_string(30074))
            kb.setHiddenInput(True)
            kb.doModal()
            if kb.isConfirmed() and kb.getText():
                new_pin = kb.getText()
                kb2 = xbmc.Keyboard('', get_string(30075))
                kb2.setHiddenInput(True)
                kb2.doModal()
                if kb2.isConfirmed() and kb2.getText() == new_pin:
                    config['pin'] = new_pin
                    save_security_config(config)
                    xbmcgui.Dialog().notification(get_string(30076), get_string(30077), xbmcgui.NOTIFICATION_INFO)
                else:
                    xbmcgui.Dialog().notification(get_string(30044), get_string(30078), xbmcgui.NOTIFICATION_ERROR)

        elif sel == 2:
            questions = [
                get_string(30221), get_string(30222), get_string(30223),
                get_string(30224), get_string(30225), get_string(30080),
            ]
            q_sel = xbmcgui.Dialog().select(get_string(30079), questions)
            if q_sel < 0:
                continue
            if q_sel == len(questions) - 1:
                kb = xbmc.Keyboard('', get_string(30081))
                kb.doModal()
                if not kb.isConfirmed() or not kb.getText():
                    continue
                selected_question = kb.getText()
            else:
                selected_question = questions[q_sel]
            kb = xbmc.Keyboard('', get_string(30082))
            kb.doModal()
            if kb.isConfirmed() and kb.getText():
                config['question'] = selected_question
                config['answer'] = kb.getText()
                save_security_config(config)
                xbmcgui.Dialog().notification(get_string(30083), get_string(30084), xbmcgui.NOTIFICATION_INFO)

        elif sel == 3:
            if not is_enabled:
                if not has_pin:
                    xbmcgui.Dialog().ok(get_string(30085), get_string(30086))
                    continue
                if not has_question:
                    if not xbmcgui.Dialog().yesno(get_string(30087), get_string(30088)):
                        continue
                config['enabled'] = True
                save_security_config(config)
                xbmcgui.Dialog().notification(get_string(30089), get_string(30090), xbmcgui.NOTIFICATION_INFO)
            else:
                config['enabled'] = False
                save_security_config(config)
                xbmcgui.Dialog().notification(get_string(30091), get_string(30092), xbmcgui.NOTIFICATION_INFO)
