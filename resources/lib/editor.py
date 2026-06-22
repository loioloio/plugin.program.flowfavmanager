# -*- coding: utf-8 -*-
"""Visual favourites editor in an XML window: reordering, multiselect and styles."""
import datetime
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import templates
from resources.lib.common import ADDON, PATHS, PROPS, get_string, log_audit, log_debug, translatePath
from resources.lib.database import FavouriteEntry, FavouritesEngine, save_profile

# Colour palettes (AARRGGBB format). The index comes from the addon's enum settings.
_SELECTION_COLORS = ['FF12A0C7', 'FF20E020', 'FFE0E020', 'FFE02020', 'FFE08020', 'FFE020E0']
_MULTISELECT_COLORS = ['FF20E020', 'FFE020E0', 'FFE0E020', 'FFFFFFFF']
_BACKGROUNDS = {
    0: ('F0101010', 'FF202020'),  # Dark (default)
    1: ('FF505050', 'FF707070'),  # Light
    2: ('FF303030', 'FF404040'),  # Neutral grey
    3: ('FF050520', 'FF101040'),  # Deep blue
}


def resolve_theme(is_colorblind, idx_bg, idx_sel, idx_multi, font_setting):
    """Compute the editor's colours and font from the settings. Pure function."""
    if is_colorblind:
        bg, bg_top, selection, multiselect = 'FF000000', 'FF404040', 'FFFFFF00', 'FFFF0000'
    else:
        bg, bg_top = _BACKGROUNDS.get(idx_bg, _BACKGROUNDS[0])
        selection = _SELECTION_COLORS[idx_sel] if 0 <= idx_sel < len(_SELECTION_COLORS) else _SELECTION_COLORS[0]
        multiselect = _MULTISELECT_COLORS[idx_multi] if 0 <= idx_multi < len(_MULTISELECT_COLORS) else _MULTISELECT_COLORS[0]

    font = {'0': 'font12', '2': 'font30'}.get(font_setting, 'font13')
    return {
        'bg': bg,
        'bg_top': bg_top,
        'selection': selection,
        'selection_faded': '60' + selection[2:],  # same colour at 60% alpha
        'multiselect': multiselect,
        'font': font,
    }


class FavouritesEditor(xbmcgui.WindowXMLDialog):
    """Editor window logic."""

    PANEL_IDS = {'0': 101, '1': 102, '2': 103, '3': 104}
    ID_BTN_CLOSE = 301
    ID_BTN_RESTORE = 302

    MOVE_SWAP = '0'
    MOVE_INSERT_BEFORE = '1'
    MOVE_INSERT_AFTER = '2'
    MOVE_ARROWS = '3'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine = FavouritesEngine()
        self.entries = []
        self.original_entries = []
        self.list_items = []
        self.drag_origin_index = None
        self.unsaved_changes = False
        self.has_pending_auto_icons = False
        self._context_menu_lock = False
        self.panel = None
        self.panel_id = 101
        self.move_behavior = self.MOVE_SWAP
        self.multiselect_active = False
        self.pending_move = False
        self.selected_indices = set()
        self.reopen_main = False

    def onInit(self):
        view_mode = self.getProperty('view_mode') or '0'
        self.panel_id = self.PANEL_IDS.get(view_mode, 101)
        try:
            self.panel = self.getControl(self.panel_id)
        except RuntimeError as e:
            log_debug(f'Panel {self.panel_id} unavailable, using 101: {e}')
            self.panel = self.getControl(101)

        # Restore the move behavior saved during the session.
        saved_behavior = xbmcgui.Window(10000).getProperty('FlowFavManager_Behavior')
        if saved_behavior:
            self.move_behavior = saved_behavior
        self._update_behavior_label()

        self.setProperty(PROPS['reorder_method'], self.move_behavior)
        self.setProperty(PROPS['font_size'], ADDON.getSetting('text_scale') or '1')
        self.setProperty(PROPS['thumb_size'], ADDON.getSetting('icon_scale') or '0')
        self.setProperty('addonIcon', os.path.join(PATHS['addon_path'], 'icon.png'))

        self.apply_color_settings()
        self.reload_data()

        try:
            self.setFocusId(self.panel_id)
            self.panel.selectItem(0)
        except RuntimeError:
            pass

    def apply_color_settings(self):
        is_colorblind = ADDON.getSetting('colorblindMode') == 'true'
        try:
            idx_bg = int(ADDON.getSetting('colorBackground') or '0')
            idx_sel = int(ADDON.getSetting('colorSelection') or '0')
            idx_multi = int(ADDON.getSetting('colorMultiselect') or '0')
        except ValueError:
            idx_bg = idx_sel = idx_multi = 0

        theme = resolve_theme(is_colorblind, idx_bg, idx_sel, idx_multi, ADDON.getSetting('text_scale') or '1')

        # Via Skin.String so the values stay accessible even when the editor loses focus
        # (e.g. when the keyboard opens), avoiding the white background when dialogs open.
        xbmc.executebuiltin(f"Skin.SetString(FavEdit_color_bg, {theme['bg']})")
        xbmc.executebuiltin(f"Skin.SetString(FavEdit_color_bg_top, {theme['bg_top']})")
        xbmc.executebuiltin(f"Skin.SetString(FavEdit_color_selection, {theme['selection']})")
        xbmc.executebuiltin(f"Skin.SetString(FavEdit_color_selection_faded, {theme['selection_faded']})")
        xbmc.executebuiltin(f"Skin.SetString(FavEdit_color_multiselect, {theme['multiselect']})")
        xbmc.executebuiltin(f"Skin.SetString(FavEdit_font_name, {theme['font']})")

    def reload_data(self):
        if not self.engine.load() and os.path.exists(PATHS['favourites']):
            # Corrupt/0-byte file on a hot reload (e.g. "discard changes"): keep what's already
            # in memory instead of replacing it with an empty list, and warn. The initial open is
            # guarded in router._open_editor, so this only triggers if the file breaks mid-session.
            xbmcgui.Dialog().ok(get_string(30387), get_string(30523))
            return
        self.entries = self.engine.entries
        self.original_entries = [FavouriteEntry(e.name, e.thumb, e.url) for e in self.entries]

        changes = self.engine.enrich_missing_icons()
        if changes > 0:
            # Icons recovered: mark as pending so the user can save them.
            self.unsaved_changes = True
            self.has_pending_auto_icons = True
            xbmcgui.Dialog().notification(get_string(30176), get_string(30454).format(changes), xbmcgui.NOTIFICATION_INFO)
        else:
            self.unsaved_changes = False
            self.has_pending_auto_icons = False

        self.refresh_view()

    def reset_to_original(self):
        if xbmcgui.Dialog().yesno(get_string(30167), get_string(30215)):
            self.entries = self.engine.load_original()
            self.unsaved_changes = True
            self.refresh_view()
            xbmcgui.Dialog().notification(get_string(30348), get_string(30441), xbmcgui.NOTIFICATION_INFO, 1000)

    def refresh_view(self):
        self.panel.reset()
        self.list_items = []
        for i, entry in enumerate(self.entries):
            li = xbmcgui.ListItem(label=entry.name)
            li.setArt({'thumb': entry.thumb})
            li.setProperty('index', str(i))
            if i in self.selected_indices:
                li.setProperty('multiselected', '1')
            self.list_items.append(li)

        self.setProperty('UnsavedChanges', 'true' if self.unsaved_changes else '')
        self.panel.addItems(self.list_items)
        # The multiselect state is reflected by the 'multiselect_active' window property;
        # control 308 is a button (not a radio), so 'selected' is not set on it here.

    def onClick(self, controlId):
        if controlId in self.PANEL_IDS.values():
            self.handle_panel_click()
        elif controlId in (301, 3001):  # Save and exit
            self.handle_close()
        elif controlId == 302:  # Backup/restore/save profile
            self.handle_restore_menu()
        elif controlId == 303:  # Help
            self.show_help()
        elif controlId == 304:  # Exit without saving
            self.handle_exit_no_save()
        elif controlId == 305:  # Add
            self.handle_add_menu()
        elif controlId == 308:  # Enter/leave multiselect
            self.toggle_multiselect()
        elif controlId == 350:  # Multiselect: cancel
            self.cancel_multiselect()
        elif controlId == 351:  # Multiselect: move here
            self.mass_move_here()
        elif controlId == 352:  # Multiselect: colour
            self.mass_color()
        elif controlId == 353:  # Multiselect: delete
            self.mass_delete()
        elif controlId == 354:  # Multiselect: affix
            self.mass_affix()
        elif controlId == 315:  # Move behavior
            self.cycle_move_behavior()
        elif controlId == 309:  # Sort
            self.sort_entries()
        elif controlId == 310:  # Reset
            self.reset_to_original()
        elif controlId == 312:  # Auto-group
            self.auto_group_by_addon()
        elif controlId == 316:  # Open the addon's main menu
            self.open_main_addon()

    def auto_group_by_addon(self):
        """Reorder the favourites grouping them by addon / action type."""
        if not xbmcgui.Dialog().yesno(get_string(30167), get_string(30135)):
            return

        def get_sort_key(entry):
            group_name = 'ZZ_Others'  # unclassified ones go last
            match = re.search(r'^plugin://([^/]+)/', entry.url)
            if not match:
                # RunAddon("addon.id") shortcuts carry the addon id too; group them like plugin:// URLs.
                match = re.search(r'RunAddon\("?([^")\s]+)"?\)', entry.url)
            if match:
                addon_id = match.group(1)
                try:
                    group_name = self._strip_tags(xbmcaddon.Addon(addon_id).getAddonInfo('name')).upper()
                except RuntimeError:
                    group_name = addon_id.upper()
            elif 'ActivateWindow' in entry.url:
                group_name = 'AAA_KODI WINDOWS'
            elif 'RunScript' in entry.url:
                group_name = 'AAA_SCRIPTS'
            elif 'StartAndroidActivity' in entry.url:
                group_name = 'ANDROID APPS'
            return group_name, self._strip_tags(entry.name).upper()

        self.entries.sort(key=get_sort_key)
        self.unsaved_changes = True
        self.refresh_view()
        xbmcgui.Dialog().notification(get_string(30101), get_string(30102), xbmcgui.NOTIFICATION_INFO)

    def show_help(self):
        help_by_mode = {
            self.MOVE_SWAP: 30104,
            self.MOVE_INSERT_BEFORE: 30105,
            self.MOVE_INSERT_AFTER: 30106,
            self.MOVE_ARROWS: 30107,
        }
        msg = get_string(help_by_mode.get(self.move_behavior, 30104))
        for sid in (30108, 30109, 30110, 30111):
            msg += get_string(sid)
        xbmcgui.Dialog().ok(get_string(30103), msg)

    def onAction(self, action):
        action_id = action.getId()
        SIDEBAR_IDS = [9000, 301, 3001, 302, 303, 304, 305, 308, 309, 310, 312, 315, 350, 351, 352, 353, 354]

        # LEFT from the sidebar returns focus to the panel. setFocusId can fail during a window
        # transition; if it does, let the action follow its normal flow.
        if action_id == 1 and self.getFocusId() in SIDEBAR_IDS:  # ACTION_MOVE_LEFT
            try:
                self.setFocusId(self.panel_id)
                return
            except RuntimeError:
                pass

        # RIGHT from the panel during multiselect goes to the mass actions. The panel has
        # onright=9000 (normal sidebar, hidden in multiselect), so focus would not get there alone.
        if action_id == 2 and self.multiselect_active and self.getFocusId() == self.panel_id:  # ACTION_MOVE_RIGHT
            try:
                self.setFocusId(351)
                return
            except RuntimeError:
                pass

        if action_id in (117, 101):  # ContextMenu / mouse right-click
            self.open_context_menu()
        elif action_id in (92, 10):  # Back / Escape
            self.handle_close()
        elif action_id == 18:  # Delete key
            self.delete_selected_item()
        elif self.move_behavior == self.MOVE_ARROWS and self.drag_origin_index is not None and self.getFocusId() == self.panel_id:
            if action_id == 3:  # UP
                self.move_with_arrows(-1)
            elif action_id == 4:  # DOWN
                self.move_with_arrows(1)
            else:
                super().onAction(action)
        else:
            super().onAction(action)

    def handle_panel_click(self):
        selected_idx = self.panel.getSelectedPosition()
        if selected_idx < 0:
            return

        if self.multiselect_active:
            if self.pending_move:
                self.execute_mass_move(selected_idx)
                return
            # Toggle selection without rebuilding the view, to keep the scroll position.
            item = self.panel.getListItem(selected_idx)
            if selected_idx in self.selected_indices:
                self.selected_indices.remove(selected_idx)
                item.setProperty('multiselected', '')
            else:
                self.selected_indices.add(selected_idx)
                item.setProperty('multiselected', '1')
            return

        # In arrows mode a click is always a "new selection", to avoid accidental swaps.
        is_arrow_mode = (self.move_behavior == self.MOVE_ARROWS)

        if self.drag_origin_index is None:
            self.drag_origin_index = selected_idx
            self.panel.getListItem(selected_idx).setProperty('selected', '1')
            self.list_items[selected_idx].setProperty('selected', '1')
            return

        if self.drag_origin_index == selected_idx:
            # Deselect the origin.
            self.panel.getListItem(selected_idx).setProperty('selected', '')
            self.list_items[selected_idx].setProperty('selected', '')
            self.drag_origin_index = None
        elif is_arrow_mode:
            # Move the selection cursor to the new item.
            self.panel.getListItem(self.drag_origin_index).setProperty('selected', '')
            self.list_items[self.drag_origin_index].setProperty('selected', '')
            self.drag_origin_index = selected_idx
            self.panel.getListItem(selected_idx).setProperty('selected', '1')
            self.list_items[selected_idx].setProperty('selected', '1')
        else:
            self.execute_reorder(self.drag_origin_index, selected_idx)

    def refresh_view_keep_selection(self, select_idx):
        self.list_items = []
        for i, entry in enumerate(self.entries):
            li = xbmcgui.ListItem(label=entry.name)
            li.setArt({'thumb': entry.thumb})
            li.setProperty('index', str(i))
            if i in self.selected_indices:
                li.setProperty('multiselected', '1')
            if i == self.drag_origin_index:
                li.setProperty('selected', '1')
            self.list_items.append(li)

        self.panel.reset()
        self.panel.addItems(self.list_items)
        if 0 <= select_idx < len(self.list_items):
            self.panel.selectItem(select_idx)

    def execute_reorder(self, idx_a, idx_b):
        mode = self.move_behavior
        if mode == self.MOVE_ARROWS:
            mode = self.MOVE_SWAP  # the secondary click in arrows mode acts as a swap

        if mode == self.MOVE_SWAP:
            # In-place swap, no refresh, to avoid scroll jumps.
            self._swap_items(idx_a, idx_b)
            self._swap_listitem_contents(idx_a, idx_b)
            self._set_selected(idx_a, '')
            self._set_selected(idx_b, '')
            self.drag_origin_index = None
            self.unsaved_changes = True
            self.setProperty('UnsavedChanges', 'true')
            try:
                self.panel.selectItem(idx_b)
            except RuntimeError:
                pass
        else:
            # Insert before/after changes the structure: a refresh is required.
            target = idx_b + 1 if mode == self.MOVE_INSERT_AFTER else idx_b
            final_focus = self._move_item_to(idx_a, target)
            self.drag_origin_index = None
            self.unsaved_changes = True
            self.refresh_view()
            self.panel.selectItem(final_focus)

    def _swap_items(self, i1, i2):
        self.entries[i1], self.entries[i2] = self.entries[i2], self.entries[i1]

    def _swap_listitem_contents(self, idx_a, idx_b):
        """Swap label/art of two ListItems (UI and in-memory mirror) without moving objects."""
        # Read both positions before writing: in Kodi panel.getListItem(i) and list_items[i]
        # share state, so reading after a partial write would undo the swap.
        item_a, item_b = self.panel.getListItem(idx_a), self.panel.getListItem(idx_b)
        lbl_a, art_a = item_a.getLabel(), item_a.getArt('thumb')
        lbl_b, art_b = item_b.getLabel(), item_b.getArt('thumb')
        for item in (item_a, self.list_items[idx_a]):
            item.setLabel(lbl_b)
            item.setArt({'thumb': art_b})
        for item in (item_b, self.list_items[idx_b]):
            item.setLabel(lbl_a)
            item.setArt({'thumb': art_a})

    def _set_selected(self, index, value):
        """Set the 'selected' property on the panel item and on its in-memory mirror."""
        self.panel.getListItem(index).setProperty('selected', value)
        self.list_items[index].setProperty('selected', value)

    def _move_item_to(self, src_idx, dest_idx):
        """Move an item from src to dest, adjusting the index after the pop."""
        dest_idx = max(0, min(dest_idx, len(self.entries)))
        item = self.entries.pop(src_idx)
        if dest_idx > src_idx:
            dest_idx -= 1
        self.entries.insert(dest_idx, item)
        return dest_idx

    def open_context_menu(self):
        if self._context_menu_lock:
            return
        self._context_menu_lock = True
        try:
            idx = self.panel.getSelectedPosition()
            if idx < 0:
                return
            header = f"[B][COLOR yellow]{self._strip_tags(self.entries[idx].name)}[/COLOR][/B]"
            opts = [header, get_string(30112), get_string(30113), get_string(30114), get_string(30115),
                    get_string(30116), get_string(30117), get_string(30118), get_string(30119), '« ' + get_string(30430)]
            actions = {
                1: self.rename_entry, 2: self.edit_entry_path, 3: self.change_icon_selected,
                4: self.style_entry_color, 5: self.style_entry_format, 6: self.quick_move_entry,
                7: self.duplicate_entry,
            }
            selection = xbmcgui.Dialog().contextmenu(opts)
            if selection in actions:
                actions[selection](idx)
            elif selection == 8:
                self.delete_selected_item()
            xbmc.sleep(200)
        finally:
            self._context_menu_lock = False

    def _strip_tags(self, text):
        text = re.sub(r'\[COLOR [^\]]+\]', '', text)
        text = re.sub(r'\[/COLOR\]', '', text)
        text = re.sub(r'\[/?(B|I|UPPERCASE|LOWERCASE)\]', '', text)
        return text.strip()

    def _is_separator(self, entry):
        # Separators are recognized by the '▬' rule in their name, a language-independent marker.
        # (The previous detection looked for the Spanish literal "Sección" in the URL, which only
        # matched separators created with the UI in Spanish.)
        return '▬' in entry.name

    def duplicate_entry(self, idx):
        """Duplicate the item and insert it right after, with a unique URL."""
        original = self.entries[idx]
        new_name = original.name + get_string(30455)

        # Kodi identifies favourites by their URL; it must be made unique or the duplicate is ignored.
        unique_id = str(int(time.time() * 1000))[-6:]
        url = original.url
        if url.endswith('/'):
            new_url = url + '?_dup=' + unique_id
        elif '?' in url:
            new_url = url + '&_dup=' + unique_id
        else:
            # Separators (Notification(...)) and plain commands: a trailing space is enough to
            # make the URL unique in favourites.xml while leaving the command itself valid and
            # unchanged (injecting '#id' inside the parens corrupted the command's arguments).
            new_url = url + ' '

        self.entries.insert(idx + 1, FavouriteEntry(new_name, original.thumb, new_url))
        self.unsaved_changes = True
        self.refresh_view()
        self.panel.selectItem(idx + 1)
        xbmcgui.Dialog().notification(get_string(30121), self._strip_tags(original.name), xbmcgui.NOTIFICATION_INFO, 2000)

    def quick_move_entry(self, idx):
        total = len(self.entries)
        opts = [get_string(sid) for sid in (30123, 30124, 30125, 30126, 30127, 30128, 30129, 30130, 30120)]
        sel = xbmcgui.Dialog().select(get_string(30122), opts)
        if sel < 0 or sel == 8:
            return

        deltas = {0: -1, 1: -5, 2: -10, 4: 1, 5: 5, 6: 10}
        if sel == 3:
            new_idx = 0
        elif sel == 7:
            new_idx = total - 1
        else:
            new_idx = max(0, min(total - 1, idx + deltas[sel]))

        entry = self.entries.pop(idx)
        self.entries.insert(new_idx, entry)
        self.unsaved_changes = True
        self.refresh_view()
        self.panel.selectItem(new_idx)

    def sort_entries(self):
        opts = [get_string(sid) for sid in (30132, 30133, 30134, 30135, 30120)]
        sel = xbmcgui.Dialog().select(get_string(30131), opts)
        if sel < 0 or sel == 4:
            return
        if sel == 3:
            self.auto_group_by_addon()
            return

        if sel == 0:
            self.entries.sort(key=lambda e: self._strip_tags(e.name).lower())
            msg = get_string(30136)
        elif sel == 1:
            self.entries.sort(key=lambda e: self._strip_tags(e.name).lower(), reverse=True)
            msg = get_string(30137)
        else:
            self.entries.reverse()
            msg = get_string(30138)

        self.unsaved_changes = True
        self.refresh_view()
        xbmcgui.Dialog().notification(get_string(30139), msg, xbmcgui.NOTIFICATION_INFO)

    def quick_save_profile(self):
        default_name = get_string(30247) + ' ' + datetime.datetime.now().strftime('%Y-%m-%d %H-%M')
        kb = xbmc.Keyboard(default_name, get_string(30035))
        kb.doModal()
        if kb.isConfirmed() and kb.getText():
            if save_profile(kb.getText(), self.entries):
                xbmcgui.Dialog().notification(get_string(30036), get_string(30141).format(kb.getText()), xbmcgui.NOTIFICATION_INFO)

    def style_entry_color(self, idx):
        entry = self.entries[idx]
        colors = [
            (get_string(30143), None), (get_string(30144), 'white'), (get_string(30145), 'yellow'),
            (get_string(30146), 'orange'), (get_string(30147), 'red'), (get_string(30148), 'pink'),
            (get_string(30149), 'violet'), (get_string(30150), 'blue'), (get_string(30151), 'cyan'),
            (get_string(30152), 'green'), (get_string(30153), 'lime'),
        ]
        sel = xbmcgui.Dialog().select(get_string(30142), [c[0] for c in colors])
        if sel < 0:
            return
        clean = self._strip_tags(entry.name)
        color_code = colors[sel][1]
        entry.name = f'[COLOR {color_code}]{clean}[/COLOR]' if color_code else clean
        self.unsaved_changes = True
        self.refresh_view_keep_selection(idx)

    def style_entry_format(self, idx):
        entry = self.entries[idx]
        formats = [
            (get_string(30155), ''), (get_string(30156), 'B'), (get_string(30157), 'I'),
            (get_string(30158), 'BI'), (get_string(30159), 'UPPERCASE'),
        ]
        sel = xbmcgui.Dialog().select(get_string(30154), [f[0] for f in formats])
        if sel < 0:
            return

        color_match = re.search(r'\[COLOR ([^\]]+)\]', entry.name)
        color_tag = color_match.group(1) if color_match else None
        clean = self._strip_tags(entry.name)
        fmt_tag = formats[sel][1]
        if 'B' in fmt_tag:
            clean = f'[B]{clean}[/B]'
        if 'I' in fmt_tag:
            clean = f'[I]{clean}[/I]'
        if 'UPPERCASE' in fmt_tag:
            clean = f'[UPPERCASE]{clean}[/UPPERCASE]'
        if color_tag:
            clean = f'[COLOR {color_tag}]{clean}[/COLOR]'

        entry.name = clean
        self.unsaved_changes = True
        self.refresh_view_keep_selection(idx)

    def rename_entry(self, idx):
        entry = self.entries[idx]
        kb = xbmc.Keyboard(self._strip_tags(entry.name), get_string(30160))
        kb.doModal()
        if kb.isConfirmed() and kb.getText():
            # Renaming drops any previous colour/format: the user edits the base text.
            entry.name = kb.getText()
            self.unsaved_changes = True
            self.refresh_view_keep_selection(idx)

    def edit_entry_path(self, idx):
        entry = self.entries[idx]
        kb = xbmc.Keyboard(entry.url, get_string(30161))
        kb.doModal()
        if kb.isConfirmed() and kb.getText().strip():
            entry.url = kb.getText().strip()
            self.unsaved_changes = True
            self.refresh_view_keep_selection(idx)
            xbmcgui.Dialog().notification(get_string(30162), get_string(30163), xbmcgui.NOTIFICATION_INFO, 1000)

    def delete_selected_item(self):
        idx = self.panel.getSelectedPosition()
        if idx < 0:
            return
        entry = self.entries[idx]

        if self._is_separator(entry):
            opts = [get_string(30164), get_string(30165), get_string(30120)]
            sel = xbmcgui.Dialog().select(get_string(30166).format(self._strip_tags(entry.name)), opts)
            if sel == -1 or sel == 2:
                return
            if sel == 0:  # Separator only
                self.entries.pop(idx)
            elif sel == 1:  # Separator and its content up to the next separator
                self.entries.pop(idx)
                while idx < len(self.entries) and not self._is_separator(self.entries[idx]):
                    self.entries.pop(idx)
        else:
            if not xbmcgui.Dialog().yesno(get_string(30167), get_string(30168).format(self._strip_tags(entry.name))):
                return
            self.entries.pop(idx)

        self.unsaved_changes = True
        new_idx = min(idx, len(self.entries) - 1)
        self.refresh_view()
        if new_idx >= 0:
            self.panel.selectItem(new_idx)

    def handle_close(self):
        if not self.unsaved_changes:
            self.close()
            return

        opts = [get_string(30170), get_string(30171), get_string(30172)]
        sel = xbmcgui.Dialog().select(get_string(30169), opts)
        if sel == 0:  # Save and exit
            if self._perform_save():
                self.close()
        elif sel == 1:  # Save and reload the profile to see the changes
            if self._perform_save():
                self.unsaved_changes = False
                self.close()
                xbmc.executebuiltin(f"LoadProfile({xbmc.getInfoLabel('System.ProfileName')})")
        elif sel == 2:  # Exit without saving
            self.close()
        # sel == -1: cancel, do nothing

    def _perform_save(self):
        """Save the list to favourites.xml. Returns True on success."""
        if self.has_pending_auto_icons:
            yes = xbmcgui.Dialog().yesno(get_string(30176), get_string(30173),
                                         yeslabel=get_string(30174), nolabel=get_string(30175))
            if not yes:
                # The user rejects the auto icons: revert them before saving.
                reverted = 0
                for entry in self.entries:
                    if getattr(entry, 'auto_icon', False):
                        entry.thumb = ''
                        entry.auto_icon = False
                        reverted += 1
                xbmcgui.Dialog().notification(get_string(30176), get_string(30177).format(reverted), xbmcgui.NOTIFICATION_INFO)

        if self.engine.save(self.engine.generate_xml(self.entries)):
            xbmcgui.Dialog().notification(get_string(30030), get_string(30178), xbmcgui.NOTIFICATION_INFO, 2000)
            log_audit('FAVOURITES_SAVED', f'List saved with {len(self.entries)} items')
            return True
        xbmcgui.Dialog().notification(get_string(30044), get_string(30179), xbmcgui.NOTIFICATION_ERROR, 3000)
        log_audit('ERROR_SAVING', 'Failed to save favourites.xml')
        return False

    def handle_exit_no_save(self):
        self.close()

    def open_main_addon(self):
        """Close the editor and open the addon's main menu.

        The caller (_open_editor) performs the navigation after doModal() returns, once this
        dialog is destroyed, to avoid a focus race on close.
        """
        if self.unsaved_changes:
            sel = xbmcgui.Dialog().select(
                get_string(30169),
                [get_string(30170), get_string(30172)],  # Save and exit / Exit without saving
            )
            if sel == 0:
                if not self._perform_save():
                    return
            elif sel != 1:  # cancel (-1) or anything else: stay in the editor
                return
            # sel == 1: discard and continue
        self.reopen_main = True
        self.close()

    def add_custom_item(self):
        tmpl = templates.load_templates()
        menu_items = [get_string(30235)]
        menu_actions = ['manual']
        for cat_key in tmpl.keys():
            menu_items.append(templates.category_display(cat_key))
            menu_actions.append(('template', cat_key))
        menu_items.append(get_string(30236))
        menu_actions.append('addons')

        sel = xbmcgui.Dialog().select(get_string(30182), menu_items)
        if sel < 0:
            return

        name, path, thumb = '', '', 'DefaultAddon.png'
        action = menu_actions[sel]

        if action == 'manual':
            kb = xbmc.Keyboard('', get_string(30237))
            kb.doModal()
            if not kb.isConfirmed() or not kb.getText():
                return
            name = kb.getText()
            kb = xbmc.Keyboard('', get_string(30184))
            kb.doModal()
            if not kb.isConfirmed():
                return
            path = kb.getText().strip()
            browse_icon = xbmcgui.Dialog().browse(1, get_string(30185), 'pictures')
            thumb = browse_icon if browse_icon else 'DefaultAddon.png'

        elif action == 'addons':
            addons = self.get_installed_addons(None)
            if not addons:
                xbmcgui.Dialog().notification(get_string(30044), get_string(30238), xbmcgui.NOTIFICATION_WARNING)
                return
            addons.sort(key=lambda x: x['name'].lower())
            s = xbmcgui.Dialog().select(get_string(30239), [a['name'] for a in addons])
            if s < 0:
                return
            sel_addon = addons[s]
            name = sel_addon['name']
            thumb = sel_addon['thumbnail']
            # RunAddon is more compatible than plugin:// as a favourite target.
            path = f"RunAddon(\"{sel_addon['addonid']}\")"

        elif isinstance(action, tuple) and action[0] == 'template':
            items = tmpl.get(action[1], [])
            if not items:
                xbmcgui.Dialog().notification(get_string(30087), get_string(30240), xbmcgui.NOTIFICATION_WARNING)
                return
            display_names = [templates.item_display(i['name']) for i in items]
            s = xbmcgui.Dialog().select(get_string(30241) + templates.category_display(action[1]), display_names)
            if s < 0:
                return
            name = templates.item_display(items[s]['name'])
            path = items[s]['path']
            thumb = items[s]['icon']

        if not path:
            path = f'Notification("{get_string(30450)}", "{get_string(30449)}", 3000)'

        self.entries.append(FavouriteEntry(name, thumb, path))
        self.unsaved_changes = True
        self.refresh_view()
        xbmc.sleep(100)
        self.setFocus(self.panel)
        self.panel.selectItem(len(self.entries) - 1)
        xbmcgui.Dialog().notification(get_string(30242), name, xbmcgui.NOTIFICATION_INFO, 2000)

    def get_installed_addons(self, type_filter):
        """List enabled addons (plugins and scripts) via JSON-RPC."""
        all_addons = []
        for addon_type in ('xbmc.python.pluginsource', 'xbmc.python.script'):
            query = {
                'jsonrpc': '2.0',
                'method': 'Addons.GetAddons',
                'params': {'properties': ['name', 'thumbnail'], 'enabled': True, 'type': addon_type},
                'id': 1,
            }
            try:
                result = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
            except ValueError as e:
                log_debug(f'Invalid JSON-RPC Addons.GetAddons response: {e}')
                continue
            # Some Kodi versions return {"result": null} on an empty/successful call; guard against
            # calling .get() on None.
            addons_result = result.get('result') or {}
            for a in addons_result.get('addons', []):
                a['type'] = addon_type
                all_addons.append(a)
        return all_addons

    def add_separator(self):
        kb = xbmc.Keyboard('', get_string(30193))
        kb.doModal()
        if not kb.isConfirmed() or not kb.getText():
            return
        name = kb.getText()

        colors = [
            (get_string(30194), 'gold'), (get_string(30144), 'white'), (get_string(30145), 'yellow'),
            (get_string(30146), 'orange'), (get_string(30147), 'red'), (get_string(30148), 'pink'),
            (get_string(30149), 'violet'), (get_string(30150), 'blue'), (get_string(30151), 'cyan'),
            (get_string(30152), 'green'), (get_string(30153), 'lime'), (get_string(30195), 'gray'),
        ]
        sel = xbmcgui.Dialog().select(get_string(30196), [c[0] for c in colors])
        color_code = colors[sel][1] if sel >= 0 else 'gold'

        # The '▬' rule in the name is what later identifies the separator (see _is_separator).
        display_name = f"[COLOR {color_code}][B]{name.upper()}[/B] ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬[/COLOR]"
        action = f'Notification("{get_string(30449)}", "{name}", 1000)'
        self.entries.append(FavouriteEntry(display_name, 'DefaultFolder.png', action))
        self.unsaved_changes = True
        self.refresh_view()
        self.panel.selectItem(len(self.entries) - 1)

    def toggle_multiselect(self):
        self.multiselect_active = True
        self.selected_indices.clear()
        self.setProperty('multiselect_active', '1')  # activates the skin's grouplist B
        self.refresh_view_keep_selection(self.panel.getSelectedPosition())
        self.setFocus(self.panel)

    def cancel_multiselect(self):
        self.multiselect_active = False
        self.pending_move = False
        self.selected_indices.clear()
        self.clearProperty('multiselect_active')
        self.refresh_view_keep_selection(self.panel.getSelectedPosition())

    def mass_move_here(self):
        if not self.selected_indices:
            xbmcgui.Dialog().notification(get_string(30044), get_string(30197), xbmcgui.NOTIFICATION_WARNING)
            return
        self.pending_move = True
        self.setFocus(self.panel)
        xbmcgui.Dialog().notification(get_string(30198), get_string(30199), xbmcgui.NOTIFICATION_INFO, 3000)

    def execute_mass_move(self, target_idx):
        insert_after = True
        if target_idx == 0:
            # On the first item: ask whether it goes to the absolute top or below the first one.
            ret = xbmcgui.Dialog().select(get_string(30167), [get_string(30200), get_string(30201)])
            if ret < 0:
                return
            if ret == 0:
                insert_after = False

        indices = sorted(self.selected_indices)
        items_to_move = [self.entries[i] for i in indices]
        for i in reversed(indices):
            self.entries.pop(i)

        # Correct the destination for the items removed before that position.
        deleted_before = sum(1 for x in indices if x < target_idx)
        insert_pos = max(0, target_idx - deleted_before + (1 if insert_after else 0))
        for item in reversed(items_to_move):
            self.entries.insert(insert_pos, item)

        self.cancel_multiselect()
        self.unsaved_changes = True
        self.refresh_view_keep_selection(insert_pos)
        xbmcgui.Dialog().notification(get_string(30202), get_string(30203).format(len(items_to_move)), xbmcgui.NOTIFICATION_INFO)

    def mass_delete(self):
        if not self.selected_indices:
            return
        if not xbmcgui.Dialog().yesno(get_string(30167), get_string(30204).format(len(self.selected_indices))):
            return
        for i in sorted(self.selected_indices, reverse=True):
            self.entries.pop(i)
        self.cancel_multiselect()
        self.unsaved_changes = True
        self.refresh_view()

    def mass_color(self):
        if not self.selected_indices:
            return
        colors = [
            (get_string(30143), None), (get_string(30144), 'white'), (get_string(30145), 'yellow'),
            (get_string(30146), 'orange'), (get_string(30147), 'red'), (get_string(30148), 'pink'),
            (get_string(30149), 'violet'), (get_string(30150), 'blue'), (get_string(30151), 'cyan'),
            (get_string(30152), 'green'), (get_string(30153), 'lime'),
        ]
        sel = xbmcgui.Dialog().select(get_string(30205).format(len(self.selected_indices)), [c[0] for c in colors])
        if sel < 0:
            return
        color_code = colors[sel][1]
        for i in self.selected_indices:
            clean = self._strip_tags(self.entries[i].name)
            self.entries[i].name = f'[COLOR {color_code}]{clean}[/COLOR]' if color_code else clean
        self.cancel_multiselect()
        self.unsaved_changes = True
        self.refresh_view()

    def mass_affix(self):
        if not self.selected_indices:
            return

        n = len(self.selected_indices)
        sel_pos = xbmcgui.Dialog().select(
            get_string(30485).format(n),
            [get_string(30486), get_string(30487)]
        )
        if sel_pos < 0:
            return

        kb = xbmc.Keyboard('', get_string(30488))
        kb.doModal()
        text = kb.getText()
        if not kb.isConfirmed() or not text.strip():
            return
        affix_text = text

        colors = [
            (get_string(30143), None), (get_string(30144), 'white'), (get_string(30145), 'yellow'),
            (get_string(30146), 'orange'), (get_string(30147), 'red'), (get_string(30148), 'pink'),
            (get_string(30149), 'violet'), (get_string(30150), 'blue'), (get_string(30151), 'cyan'),
            (get_string(30152), 'green'), (get_string(30153), 'lime'),
        ]
        sel_col = xbmcgui.Dialog().select(get_string(30489), [c[0] for c in colors])
        # sel_col < 0 means Escape/Back — treat as "no colour" (same as picking index 0)
        # rather than aborting, so the text the user just typed is not silently discarded.
        color_code = colors[sel_col][1] if sel_col >= 0 else None

        # Build the formatted affix: only wrap in [COLOR] if a colour was chosen
        if color_code:
            formatted_affix = f'[COLOR {color_code}]{affix_text}[/COLOR]'
        else:
            formatted_affix = affix_text

        # Use sorted indices for deterministic order (same pattern as mass_delete)
        for i in sorted(self.selected_indices):
            if i < 0 or i >= len(self.entries):  # guard against stale index
                continue
            entry = self.entries[i]
            if sel_pos == 0:  # Prefix
                entry.name = formatted_affix + entry.name
            else:  # Suffix
                entry.name = entry.name + formatted_affix

        self.cancel_multiselect()
        self.unsaved_changes = True
        self.refresh_view()

    def change_icon_selected(self, idx=None):
        if idx is None:
            idx = self.panel.getSelectedPosition()
        if idx < 0:
            xbmcgui.Dialog().notification(get_string(30044), get_string(30197), xbmcgui.NOTIFICATION_WARNING, 2000)
            return
        browse_icon = xbmcgui.Dialog().browse(1, get_string(30206), 'pictures')
        if browse_icon:
            self.entries[idx].thumb = browse_icon
            self.unsaved_changes = True
            self.refresh_view_keep_selection(idx)
            xbmcgui.Dialog().notification(get_string(30030), get_string(30208), xbmcgui.NOTIFICATION_INFO, 1500)

    def handle_add_menu(self):
        sel = xbmcgui.Dialog().select(get_string(30211), [get_string(30209), get_string(30210)])
        if sel == 0:
            self.add_custom_item()
        elif sel == 1:
            self.add_separator()

    def handle_restore_menu(self):
        opts = [get_string(30212), get_string(30213), get_string(30214), get_string(30215)]
        sel = xbmcgui.Dialog().select(get_string(30216), opts)
        if sel == 0:
            self.quick_save_profile()
        elif sel == 1:
            self.do_backup_create()
        elif sel == 2:
            self.do_backup_restore()
        elif sel == 3:
            self.reload_data()
            self.unsaved_changes = False

    def do_backup_create(self):
        default_name = 'favourites_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        kb = xbmc.Keyboard(default_name, get_string(30383))
        kb.doModal()
        if not kb.isConfirmed() or not kb.getText():
            return
        folder = xbmcgui.Dialog().browse(0, get_string(30388), 'files')
        if not folder:
            return
        path = os.path.join(translatePath(folder), kb.getText() + '.xml')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.engine.generate_xml(self.entries))
            xbmcgui.Dialog().notification(get_string(30347), kb.getText(), xbmcgui.NOTIFICATION_INFO, 3000)
        except OSError as e:
            xbmcgui.Dialog().ok(get_string(30044), str(e))

    def do_backup_restore(self):
        file_path = xbmcgui.Dialog().browse(1, get_string(30389), 'files', '.xml')
        if not file_path:
            return
        full_path = translatePath(file_path)
        if not os.path.exists(full_path):
            xbmcgui.Dialog().ok(get_string(30044), get_string(30365))
            return
        try:
            root = ET.parse(full_path).getroot()
        except ET.ParseError as e:
            xbmcgui.Dialog().ok(get_string(30044), get_string(30367) + "\n" + str(e))
            return
        if root.tag != 'favourites':
            xbmcgui.Dialog().ok(get_string(30044), get_string(30366))
            return
        self.entries = [FavouriteEntry.from_xml_element(c) for c in root if c.tag == 'favourite']
        self.unsaved_changes = True
        self.refresh_view()
        xbmcgui.Dialog().notification(get_string(30348), get_string(30349), xbmcgui.NOTIFICATION_INFO, 3000)

    def _update_behavior_label(self):
        labels = {
            self.MOVE_SWAP: get_string(30431),
            self.MOVE_INSERT_BEFORE: get_string(30432),
            self.MOVE_INSERT_AFTER: get_string(30433),
            self.MOVE_ARROWS: get_string(30434),
        }
        self.setProperty('flow_action_label', f"[B]{labels.get(self.move_behavior, '?')}[/B]")
        xbmcgui.Window(10000).setProperty('FlowFavManager_Behavior', self.move_behavior)
        self.setProperty(PROPS['reorder_method'], self.move_behavior)

    def cycle_move_behavior(self):
        modes = [self.MOVE_SWAP, self.MOVE_INSERT_BEFORE, self.MOVE_INSERT_AFTER, self.MOVE_ARROWS]
        try:
            current_idx = modes.index(self.move_behavior)
        except ValueError:
            current_idx = 0
        self.move_behavior = modes[(current_idx + 1) % len(modes)]
        self._update_behavior_label()
        labels = {
            self.MOVE_SWAP: get_string(30431),
            self.MOVE_INSERT_BEFORE: get_string(30432),
            self.MOVE_INSERT_AFTER: get_string(30433),
            self.MOVE_ARROWS: get_string(30434),
        }
        xbmcgui.Dialog().notification(get_string(30350), labels[self.move_behavior], xbmcgui.NOTIFICATION_INFO, 2000)

    def move_with_arrows(self, direction):
        """Move the selected item one position up (-1) or down (+1), with wrap-around."""
        idx = self.drag_origin_index
        total = len(self.entries)
        new_idx = idx + direction
        if new_idx < 0:
            new_idx = total - 1
        elif new_idx >= total:
            new_idx = 0
        if new_idx == idx:
            return

        self._swap_items(idx, new_idx)
        self._swap_listitem_contents(idx, new_idx)
        self._set_selected(idx, '')
        self._set_selected(new_idx, '1')  # the selection cursor travels with the item

        self.drag_origin_index = new_idx
        self.unsaved_changes = True
        self.setProperty('UnsavedChanges', 'true')
        try:
            self.panel.selectItem(new_idx)
        except RuntimeError:
            pass
