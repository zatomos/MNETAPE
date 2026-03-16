"""Action management and context-menu handling for the main window.

ActionController manages the action list: adding, removing, reordering, editing, and reconciling actions
from manually edited code. It also handles right-click context menus for actions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gi.repository import Gdk, Gio, GLib, Gtk

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import parse_script_to_actions
from mnetape.core.models import ActionConfig, ICASolution
from mnetape.gui.dialogs import ActionEditor, AddActionDialog

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)

class ActionController:
    """Manages the action list and the action editor dialog."""

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.state = window.state
        self.pending_code: str | None = None
        self.code_edit_timer: int | None = None  # GLib source id

    def add_action(self, _btn=None):
        """Open the Add Action dialog and append the selected action to the pipeline."""
        dialog = AddActionDialog(self.w.window)
        if dialog.exec():
            action_id = dialog.get_action_id()
            if action_id:
                action_def = get_action_by_id(action_id)
                params = action_def.default_params() if action_def else {}
                action = ActionConfig(action_id, params)
                self.state.actions.append(action)
                self.w.update_action_list()

                row = len(self.state.actions) - 1
                self.w.set_selected_action_row(row)
                self.edit_action(row)

                logger.info("Added action: %s", get_action_title(action))

    def remove_action(self, _action=None, _param=None):
        """Remove the currently selected action from the pipeline."""
        row = self.w.get_selected_action_row()
        if row >= 0:
            self.state.actions.pop(row)
            self.state.data_states.truncate(row)
            for action in self.state.actions[row:]:
                if not action.is_custom:
                    action.reset()
            self.w.update_action_list()

    def move_action(self, direction: int):
        """Move the currently selected action up or down in the pipeline.

        Args:
            direction: -1 for up, +1 for down.
        """
        row = self.w.get_selected_action_row()
        new_row = row + direction
        if 0 <= new_row < len(self.state.actions):
            self.state.actions[row], self.state.actions[new_row] = (
                self.state.actions[new_row],
                self.state.actions[row],
            )
            self.state.data_states.truncate(min(row, new_row))
            for action in self.state.actions[min(row, new_row):]:
                if not action.is_custom:
                    action.reset()
            self.w.update_action_list()
            self.w.set_selected_action_row(new_row)
            self.w.update_button_states()

    def move_action_from_to(self, from_row: int, to_row: int):
        """Move the action at from_row to the position of to_row (drag-and-drop).

        Args:
            from_row: Source action index.
            to_row: Destination action index.
        """
        if from_row == to_row:
            return
        n = len(self.state.actions)
        if not (0 <= from_row < n and 0 <= to_row < n):
            return
        action = self.state.actions.pop(from_row)
        self.state.actions.insert(to_row, action)
        min_row = min(from_row, to_row)
        self.state.data_states.truncate(min_row)
        for a in self.state.actions[min_row:]:
            if not a.is_custom:
                a.reset()
        self.w.update_action_list()
        self.w.set_selected_action_row(to_row)
        self.w.update_button_states()

    def on_action_row_selected(self, _list_box, row):
        """Respond to an action list row being selected."""
        if row is None:
            self.w.update_button_states()
            return
        r = getattr(row, "_action_row", -1)
        if r >= 0:
            self.w.update_button_states()
            self.w.update_visualization(r)

    def on_action_row_activated(self, _list_box, row):
        """On double-click on an action row."""
        r = getattr(row, "_action_row", -1)
        if r >= 0:
            self.w.set_selected_action_row(r)
            self.edit_action(r)

    def show_action_context_menu(self, row_index: int, x: float, y: float, widget: Gtk.Widget):
        """Show a right-click context menu for the action at row_index.

        Args:
            row_index: Index of the action.
            x: X position (widget-local).
            y: Y position (widget-local).
            widget: The widget the gesture fired on.
        """
        if row_index < 0:
            return
        self.w.set_selected_action_row(row_index)

        menu = Gtk.PopoverMenu()
        menu.set_parent(widget)

        g_menu = Gio.Menu()
        g_menu.append("Edit Settings...", "row.edit")
        g_menu.append("Run This", "row.run_single")
        g_menu.append("Run This and Above", "row.run_to")

        action = self.state.actions[row_index]
        if action.result is not None:
            section = Gio.Menu()
            section.append("View Results", "row.view_results")
            g_menu.append_section(None, section)

        section2 = Gio.Menu()
        section2.append("Export data at this step...", "row.export")
        g_menu.append_section(None, section2)

        section3 = Gio.Menu()
        section3.append("Remove", "row.remove")
        g_menu.append_section(None, section3)

        menu.set_menu_model(g_menu)

        # Add actions to widget action group
        ag = Gio.SimpleActionGroup()

        def add_menu_item(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            ag.add_action(a)

        add_menu_item("edit", lambda *_: self.edit_action(row_index))
        add_menu_item("run_single", lambda *_: self.w.runner.run_action_at(row_index))
        add_menu_item("run_to", lambda *_: (self.w.set_selected_action_row(row_index), self.w.runner.run_to_selected()))
        add_menu_item("view_results", lambda *_: self.w.open_action_results(row_index))
        add_menu_item("export", lambda *_: self.w.files.export_file(row_index))
        add_menu_item("remove", lambda *_: (self.w.set_selected_action_row(row_index), self.remove_action()))

        widget.insert_action_group("row", ag)

        rect = gdk_rect(x, y)
        menu.set_pointing_to(rect)
        menu.popup()

    def on_manual_code_edit(self, code: str):
        """Buffer a manual code edit and start the debounce timer."""
        if self.code_edit_timer is not None:
            GLib.source_remove(self.code_edit_timer)
        self.pending_code = code
        self.code_edit_timer = GLib.timeout_add(500, self.apply_manual_code_edit_idle)

    def apply_manual_code_edit_idle(self) -> bool:
        """GLib idle callback that applies the buffered code edit."""
        self.code_edit_timer = None
        self.apply_manual_code_edit()
        return False  # Don't repeat

    def apply_manual_code_edit(self):
        """Apply the buffered code edit to the action list."""
        code = self.pending_code
        new_actions = parse_script_to_actions(code)
        changed = False
        first_changed_idx: int | None = None
        old_count = len(self.state.actions)

        for i, (old, new) in enumerate(zip(self.state.actions, new_actions)):
            if old.action_id != new.action_id:
                self.state.actions[i] = new
                if first_changed_idx is None:
                    first_changed_idx = i
                changed = True
                continue

            if (
                old.params != new.params
                or old.advanced_params != new.advanced_params
                or old.custom_code != new.custom_code
                or old.is_custom != new.is_custom
                or old.title_override != new.title_override
            ):
                old.params = new.params
                old.advanced_params = new.advanced_params
                old.custom_code = new.custom_code
                old.is_custom = new.is_custom
                old.title_override = new.title_override
                if first_changed_idx is None:
                    first_changed_idx = i
                changed = True

        if len(new_actions) > old_count:
            for new_action in new_actions[old_count:]:
                self.state.actions.append(new_action)
            if first_changed_idx is None:
                first_changed_idx = old_count
            changed = True

        if len(new_actions) < old_count:
            self.state.actions = self.state.actions[: len(new_actions)]
            if first_changed_idx is None:
                first_changed_idx = len(new_actions)
            changed = True

        if changed:
            if first_changed_idx is None:
                first_changed_idx = 0
            self.state.data_states.truncate(first_changed_idx)
            for action in self.state.actions[first_changed_idx:]:
                action.reset()

        self.w.update_action_list(sync_code=False)
        if changed:
            logger.info("Applied manual code edits; action list updated")
        self.w.files.auto_save()

    def edit_action(self, row: int):
        """Open the action editor dialog for the action at row."""
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]

        if row == 0:
            current_raw = self.state.raw_original
        elif row <= len(self.state.data_states):
            stored = self.state.data_states[row - 1]
            if isinstance(stored, ICASolution):
                current_raw = stored
            elif hasattr(stored, "ch_names"):
                current_raw = stored.copy()
            else:
                current_raw = self.state.raw_original
        else:
            current_raw = self.state.raw_original

        context_type = self.w.runner.get_data_type_at(row)
        dialog = ActionEditor(action, current_raw, self.w.window, context_type=context_type)
        if dialog.exec():
            action.params = dialog.get_params()
            action.advanced_params = dialog.get_advanced_params()

            if dialog.should_clear_custom():
                action.custom_code = ""
                action.is_custom = False

            if not action.is_custom:
                action.reset()
            self.state.data_states.truncate(row)
            for a in self.state.actions[row:]:
                if not a.is_custom:
                    a.reset()
            self.w.update_action_list()

def gdk_rect(x: float, y: float):
    """Create a Gdk.Rectangle pointing at (x, y)."""
    rect = Gdk.Rectangle()
    rect.x = int(x)
    rect.y = int(y)
    rect.width = 1
    rect.height = 1
    return rect
