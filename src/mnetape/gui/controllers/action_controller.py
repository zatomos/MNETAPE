"""Action management and context-menu handling for the main window.

ActionController manages the action list: adding, removing, reordering, editing, and reconciling actions
from manually edited code. It also handles right-click context menus for multistep actions.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QMenu, QMessageBox

from typing import TYPE_CHECKING

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import parse_script_to_actions
from mnetape.core.models import ActionConfig, ActionStatus
from mnetape.gui.dialogs import ActionEditor, AddActionDialog

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)


def run_step_keep_menu(fn, menu: QMenu, global_pos):
    """Execute a step action and then re-open the context menu at the same position. [NOT FUNCTIONAL YET]

    Args:
        fn: Callable to execute.
        menu: The QMenu to re-open after fn returns.
        global_pos: Screen-space position at which to re-show the menu.
    """
    fn()
    QTimer.singleShot(0, lambda: menu.popup(global_pos))


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
                self.w.action_list.setCurrentRow(row)
                self.edit_action(row)

                logger.info("Added action: %s", get_action_title(action))

    def remove_action(self):
        """Remove the currently selected action from the pipeline."""
        row = self.w.action_list.currentRow()
        if row >= 0:
            self.state.actions.pop(row)
            self.state.raw_states = self.state.raw_states[:row]
            for action in self.state.actions[row:]:
                if not action.is_custom:
                    action.reset()
            self.w.update_action_list()

    def move_action(self, direction: int):
        """Move the currently selected action up or down in the pipeline.

        Args:
            direction: -1: up, +1: down.
        """
        row = self.w.action_list.currentRow()
        new_row = row + direction
        if 0 <= new_row < len(self.state.actions):
            self.state.actions[row], self.state.actions[new_row] = self.state.actions[new_row], self.state.actions[row]
            self.state.raw_states = self.state.raw_states[: min(row, new_row)]
            for action in self.state.actions[min(row, new_row) :]:
                if not action.is_custom:
                    action.reset()
            self.w.update_action_list()
            self.w.action_list.setCurrentRow(new_row)
            self.w.update_button_states()

    def on_action_clicked(self):
        """Respond to an action item being clicked in the list."""
        row = self.w.action_list.currentRow()
        self.w.update_button_states()
        self.w.viz_panel.step_combo.setCurrentIndex(row + 1)

    def show_action_context_menu(self, pos):
        """Show a right-click context menu for the action item at the given position.

        For multistep actions, the menu includes step-level run and reset entries.
        For single-step actions, a plain run entry is shown instead.

        Args:
            pos: Widget-local cursor position from the contextMenuRequested signal.
        """
        item = self.w.action_list.itemAt(pos)
        if item is None:
            return
        row = self.w.action_list.row(item)
        self.w.action_list.setCurrentRow(row)

        action = self.state.actions[row]
        action_def = get_action_by_id(action.action_id)
        menu = QMenu(self.w)
        global_pos = self.w.action_list.viewport().mapToGlobal(pos)

        edit_action = menu.addAction("Edit Settings...")
        edit_action.triggered.connect(lambda: self.on_action_double_clicked())

        menu.addSeparator()

        # Steps sub-menu if applicable
        if action_def and action_def.has_steps():
            next_step_idx = action.completed_steps
            if next_step_idx < len(action_def.steps):
                step = action_def.steps[next_step_idx]
                run_next = menu.addAction(f"Run Next Step: {step.title}")
                run_next.triggered.connect(
                    lambda _=False, r=row, m=menu, p=global_pos: run_step_keep_menu(
                        lambda: self.w.runner.run_single_step(r), m, p
                    )
                )
            run_remaining = menu.addAction("Run All Remaining Steps")
            run_remaining.triggered.connect(
                lambda _=False, r=row, m=menu, p=global_pos: run_step_keep_menu(
                    lambda: self.w.runner.run_remaining_steps(r), m, p
                )
            )
            if action.completed_steps > 0:
                reset_act = menu.addAction("Reset Steps")
                reset_act.triggered.connect(
                    lambda _=False, r=row, m=menu, p=global_pos: run_step_keep_menu(
                        lambda: self.w.runner.reset_action_steps(r), m, p
                    )
                )
        # If no steps, or all steps complete
        else:
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
        row = self.w.action_list.currentRow()
        if row >= 0:
            self.edit_action(row)

    def on_step_clicked(self, row: int, step_idx: int):
        """Handle a click on a step label within an action list item.

        For interactive steps, runs the interactive_runner if prerequisites are met.
        For non-interactive steps with params, opens the step editor.

        Args:
            row: Index of the action in the pipeline list.
            step_idx: Index of the step within the action.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        action_def = get_action_by_id(action.action_id)
        if not action_def or not action_def.steps:
            return
        if step_idx < 0 or step_idx >= len(action_def.steps):
            return
        step = action_def.steps[step_idx]
        if step.interactive:
            if self.state.raw_original is None:
                QMessageBox.warning(self.w, "No Data", "Load a FIF file first.")
                return
            if step_idx >= action.completed_steps:
                QMessageBox.warning(self.w, "Steps Incomplete", "Run the previous steps first.")
                return
            try:
                raw = self.w.runner.get_step_input_raw(action, row)
                runner = step.interactive_runner
                if not runner:
                    raise RuntimeError("No interactive runner configured for this action.")
                new_raw = runner(action, raw, parent=self.w)
                if new_raw is None:
                    return
                self.w.runner.store_action_raw(row, new_raw)
                action.completed_steps = max(action.completed_steps, step_idx + 1)
                if action.completed_steps >= len(action_def.steps):
                    action.status = ActionStatus.COMPLETE
                self.w.update_action_list()
                self.w.update_visualization()
            except Exception as e:
                action.status = ActionStatus.ERROR
                action.error_msg = str(e)
                self.w.update_action_list()
                logger.exception("Interactive step failed for action_id=%s step_idx=%s",
                                 action.action_id, step_idx)
                QMessageBox.critical(self.w, "Error", f"Interactive step failed:\n{e}")
        else:
            # Non-interactive step: open editor only if step has params
            if step.template_schema and step.template_schema.all_primary_params():
                self.edit_action(row, step_idx=step_idx)
                return

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

        Performs a minimal diff: only actions whose id, params, or code actually changed are reset, and raw_states
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
                or old.custom_code != new.custom_code
                or old.is_custom != new.is_custom
                or old.title_override != new.title_override
            ):
                old.params = new.params
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
            self.state.raw_states = self.state.raw_states[:first_changed_idx]
            for action in self.state.actions[first_changed_idx:]:
                action.reset()

        self.w.update_action_list(sync_code=False)
        if changed:
            logger.info("Applied manual code edits; action list updated")
        self.w.files.auto_save()

    def edit_action(self, row: int, *, step_idx: int | None = None):
        """Open the action editor dialog for the action at row.

        When step_idx is given, only the params for that step are shown and updated on accept.
        Otherwise, all action params are shown and replaced.

        Args:
            row: Index of the action to edit.
            step_idx: Optional index of the specific step to edit. When None,
                the full action editor is shown.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        if row == 0:
            current_raw = self.state.raw_original
        elif row <= len(self.state.raw_states):
            current_raw = self.state.raw_states[row - 1]
        else:
            current_raw = self.state.raw_original

        dialog = ActionEditor(action, current_raw, self.w, step_idx=step_idx)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # If editing a specific step, only update params for that step; otherwise update all params
            if step_idx is not None:
                # Merge step-specific params into the full action params
                action.params.update(dialog.get_params())
                adv = dialog.get_advanced_params()
                for func, params in adv.items():
                    action.advanced_params.setdefault(func, {}).update(params)
            else:
                action.params = dialog.get_params()
                action.advanced_params = dialog.get_advanced_params()

            # If user chose to clear custom code, reset to default and mark as non-custom
            if dialog.should_clear_custom():
                action.custom_code = ""
                action.is_custom = False

            # If the action was previously marked as custom but now matches the default code, reset it
            if not action.is_custom:
                action.reset()
            self.state.raw_states = self.state.raw_states[:row]
            for a in self.state.actions[row:]:
                if not a.is_custom:
                    a.reset()
            self.w.update_action_list()
