"""Action management and context-menu handling for the main window.

ActionController manages the action list: adding, removing, reordering, editing, and reconciling actions
from manually edited code. It also handles right-click context menus for actions.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QMenu, QMessageBox

from typing import TYPE_CHECKING

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import parse_script_to_actions
from mnetape.core.models import ActionConfig
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
        self.code_edit_timer: QTimer | None = None

    def add_action(self):
        """Open the Add Action dialog and append the selected action to the pipeline."""
        dialog = AddActionDialog(self.w)
        if dialog.exec() == QDialog.DialogCode.Accepted:
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

    def remove_action(self):
        """Remove the currently selected action from the pipeline."""
        row = self.w.get_selected_action_row()
        if row >= 0:
            self.state.actions.pop(row)
            self.state.data_states = self.state.data_states[:row]
            for action in self.state.actions[row:]:
                if not action.is_custom:
                    action.reset()
            self.w.update_action_list()

    def move_action(self, direction: int):
        """Move the currently selected action up or down in the pipeline.

        Args:
            direction: -1: up, +1: down.
        """
        row = self.w.get_selected_action_row()
        new_row = row + direction
        if 0 <= new_row < len(self.state.actions):
            self.state.actions[row], self.state.actions[new_row] = self.state.actions[new_row], self.state.actions[row]
            self.state.data_states = self.state.data_states[: min(row, new_row)]
            for action in self.state.actions[min(row, new_row) :]:
                if not action.is_custom:
                    action.reset()
            self.w.update_action_list()
            self.w.set_selected_action_row(new_row)
            self.w.update_button_states()

    def on_action_clicked(self):
        """Respond to an action item being clicked in the list."""
        row = self.w.get_selected_action_row()
        if row < 0:
            return
        self.w.update_button_states()
        self.w.viz_panel.step_combo.setCurrentIndex(row + 1)

    def show_action_context_menu(self, pos):
        """Show a right-click context menu for the action item at the given position.

        Args:
            pos: Widget-local cursor position from the contextMenuRequested signal.
        """
        item = self.w.action_list.itemAt(pos)
        if item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, int) or row < 0:
            return
        self.w.set_selected_action_row(row)

        menu = QMenu(self.w)
        global_pos = self.w.action_list.viewport().mapToGlobal(pos)

        edit_action = menu.addAction("Edit Settings...")
        edit_action.triggered.connect(lambda: self.on_action_double_clicked())

        menu.addSeparator()

        run_single_action = menu.addAction("Run This")
        run_single_action.triggered.connect(self.w.runner.run_single)

        run_to_action = menu.addAction("Run This and Above")
        run_to_action.triggered.connect(self.w.runner.run_to_selected)

        menu.addSeparator()

        export_action = menu.addAction("Export data at this step...")
        export_action.triggered.connect(lambda: self.w.files.export_file(row))

        menu.addSeparator()

        remove_action = menu.addAction("Remove")
        remove_action.triggered.connect(self.remove_action)

        menu.popup(global_pos)

    def on_action_double_clicked(self):
        """Open the action editor when the user double-clicks an action."""
        row = self.w.get_selected_action_row()
        if row >= 0:
            self.edit_action(row)

    def on_manual_code_edit(self, code: str):
        """Buffer a manual code edit and start the debounce timer.

        The edit is not applied immediately but after a short delay.

        Args:
            code: Current editor content after the edit.
        """
        if self.code_edit_timer is None:
            self.code_edit_timer = QTimer()
            self.code_edit_timer.setSingleShot(True)
            self.code_edit_timer.timeout.connect(self.apply_manual_code_edit)
        self.pending_code = code
        self.code_edit_timer.start(500)

    def apply_manual_code_edit(self):
        """Apply the buffered code edit to the action list.

        Performs a minimal diff: only actions whose id, params, or code actually changed are reset, and data_states
        are trimmed from the first changed index onward.
        """
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
            self.state.data_states = self.state.data_states[:first_changed_idx]
            for action in self.state.actions[first_changed_idx:]:
                action.reset()

        self.w.update_action_list(sync_code=False)
        if changed:
            logger.info("Applied manual code edits; action list updated")
        self.w.files.auto_save()

    def edit_action(self, row: int):
        """Open the action editor dialog for the action at row.

        Args:
            row: Index of the action to edit.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        # Pass the most relevant data object for channel-aware and ICA-aware widgets.
        # ICASolution is passed as-is so ica_apply's Browse widget can open the dialog.
        from mnetape.core.models import ICASolution
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

        dialog = ActionEditor(action, current_raw, self.w)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            action.params = dialog.get_params()
            action.advanced_params = dialog.get_advanced_params()

            if dialog.should_clear_custom():
                action.custom_code = ""
                action.is_custom = False

            if not action.is_custom:
                action.reset()
            self.state.data_states = self.state.data_states[:row]
            for a in self.state.actions[row:]:
                if not a.is_custom:
                    a.reset()
            self.w.update_action_list()
