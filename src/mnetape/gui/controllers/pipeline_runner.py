"""Pipeline execution for the main window.

PipelineRunner orchestrates running actions, manages threading workers for non-interactive
processing, and handles cancellation.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import mne
from gi.repository import Adw, GLib, Gtk

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.executor import exec_action
from mnetape.core.models import ActionResult, ActionStatus, DataType, ICASolution

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)

class OperationCancelled(Exception):
    """Raised when a long-running operation is canceled by the user."""

class PipelineRunner:
    """Orchestrates action execution for the main window.

    All heavy processing runs inside a background threading.Thread via run_in_thread().
    UI callbacks are dispatched back to the main thread via GLib.idle_add().
    """

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.state = window.state
        self.current_toast: Adw.Toast | None = None
        self.cancel_label = None

    # -------- Helpers --------

    def show_toast(self, action, title: str) -> None:
        """Show an Adw.Toast for a completed action, with an optional 'View Results' button."""
        if self.current_toast is not None:
            self.current_toast.dismiss()
            self.current_toast = None

        toast = Adw.Toast(title=f'"{title}" complete')
        toast.set_timeout(4)

        if isinstance(action.result, ActionResult):
            toast.set_button_label("View Results")
            res = action.result

            def _on_button(_t):
                self.w.show_action_result(res, title)

            toast.connect("button-clicked", _on_button)

        def _on_dismissed(_t):
            self.current_toast = None

        toast.connect("dismissed", _on_dismissed)
        self.current_toast = toast
        self.w.toast_overlay.add_toast(toast)



    def require_data(self) -> bool:
        """Show a warning and return False when no EEG file is loaded."""
        if self.state.raw_original is None:
            dlg = Adw.AlertDialog(heading="No Data", body="Load a FIF file first.")
            dlg.add_response("ok", "OK")
            dlg.set_default_response("ok")
            dlg.present(self.w.window)
            return False
        return True

    def get_data_type_at(self, row: int) -> DataType:
        """Return the DataType flowing into the action at row."""
        current_type = DataType.RAW
        for action in self.state.actions[:row]:
            action_def = get_action_by_id(action.action_id)
            if action_def and action_def.output_type != DataType.ANY:
                current_type = action_def.output_type
        return current_type

    @staticmethod
    def infer_data_type(data) -> DataType:
        """Infer DataType from a concrete data object instance."""
        if isinstance(data, ICASolution):
            return DataType.ICA
        if isinstance(data, mne.BaseEpochs):
            return DataType.EPOCHS
        if isinstance(data, mne.Evoked):
            return DataType.EVOKED
        return DataType.RAW

    def get_data_for_action(self, row: int):
        """Return a copy of the data object to pass into the action at row."""
        if 0 < row <= len(self.state.data_states):
            stored = self.state.data_states[row - 1]
            if stored is not None:
                return stored.copy()
        return self.state.raw_original.copy()

    def store_action_result(self, row, data):
        """Store the processed data object at the given pipeline position."""
        while len(self.state.data_states) < row:
            pad_index = len(self.state.data_states)
            pad_type = self.get_data_type_at(pad_index)
            if pad_type == DataType.RAW:
                self.state.data_states.append(self.state.raw_original.copy())
            else:
                self.state.data_states.append(None)
        if row < len(self.state.data_states):
            self.state.data_states[row] = data
        else:
            self.state.data_states.append(data)

    def check_prerequisites(self, action_idx: int) -> bool:
        """Check if all prerequisite actions have been run, prompting if not."""
        action = self.state.actions[action_idx]
        action_def = get_action_by_id(action.action_id)
        if not action_def or not action_def.prerequisites:
            return True

        preceding = self.state.actions[:action_idx]
        completed_ids = {a.action_id for a in preceding if a.status == ActionStatus.COMPLETE}

        warnings: list[str] = []
        for prereq in action_def.prerequisites:
            if prereq.action_id not in completed_ids:
                warnings.append(prereq.message)

        if not warnings:
            return True

        text = "\n".join(f"• {w}" for w in warnings)
        result = [False]
        loop = GLib.MainLoop()

        dlg = Adw.AlertDialog(
            heading=f"Missing Prerequisites for {action_def.title}",
            body=f"{text}\n\nContinue anyway?",
        )
        dlg.add_response("no", "No")
        dlg.add_response("yes", "Yes")
        dlg.set_default_response("no")
        dlg.set_close_response("no")

        def _on_response(_d, response):
            result[0] = response == "yes"
            loop.quit()

        dlg.connect("response", _on_response)
        dlg.present(self.w.window)
        loop.run()
        return result[0]

    def ensure_previous_actions(self, row) -> bool:
        """Ensure that all actions before row have been executed."""
        if row <= 0 or row <= len(self.state.data_states):
            return True

        result = [False]
        loop = GLib.MainLoop()

        dlg = Adw.AlertDialog(
            heading="Run Previous?",
            body="Previous actions haven't been run. Run them first?",
        )
        dlg.add_response("no", "No")
        dlg.add_response("yes", "Yes")
        dlg.set_default_response("yes")
        dlg.set_close_response("no")

        def _on_response(_d, response):
            result[0] = response == "yes"
            loop.quit()

        dlg.connect("response", _on_response)
        dlg.present(self.w.window)
        loop.run()

        if result[0]:
            self.run_actions(len(self.state.data_states), row)
        return row <= len(self.state.data_states)

    def run_in_thread(self, fn, message: str = "Processing..."):
        """Execute a callable in a background thread with a cancellable progress dialog.

        This method blocks the calling code (via a GLib.MainLoop) until the background
        thread completes or the user cancels. It must be called from the GTK main thread.
        """
        result: list[object | None] = [None]
        error: list[Exception | None] = [None]
        cancel_requested = [False]
        loop = GLib.MainLoop()

        # Progress dialog
        dialog = Adw.Dialog()
        dialog.set_title(message)
        dialog.set_content_width(300)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())
        dialog.set_child(toolbar_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        toolbar_view.set_content(content)

        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_size_request(40, 40)
        content.append(spinner)

        label = Gtk.Label(label=message)
        label.set_wrap(True)
        content.append(label)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.set_halign(Gtk.Align.CENTER)
        content.append(cancel_btn)
        self.cancel_label = label

        def _on_cancel(_btn):
            if not cancel_requested[0]:
                cancel_requested[0] = True
                label.set_text("Cancelling...")
                cancel_btn.set_sensitive(False)

        cancel_btn.connect("clicked", _on_cancel)

        def _worker():
            try:
                if not cancel_requested[0]:
                    result[0] = fn()
            except BaseException as e:
                error[0] = e
            finally:
                GLib.idle_add(_on_done)

        def _on_done():
            dialog.close()
            loop.quit()
            return False

        dialog.present(self.w.window)
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        loop.run()

        if cancel_requested[0]:
            self.w.set_status("Operation cancelled.")
            raise OperationCancelled("Operation cancelled.")

        if error[0]:
            raise error[0]
        return result[0]

    def execute_action(
        self,
        action,
        call_site: str,
        func_defs: str,
        data,
        input_type: DataType = DataType.RAW,
        output_type: DataType = DataType.RAW,
    ):
        """Execute a single action and return the resulting data object."""
        title = get_action_title(action)
        return self.run_in_thread(
            lambda cs=call_site, fd=func_defs, d=data: exec_action(
                cs, fd, d, action, input_type=input_type, output_type=output_type
            ),
            f"Running: {title}...",
        )

    # -------- Action-level execution --------

    def run_to_selected(self):
        """Run all actions from the beginning up to and including the selected row."""
        row = self.w.get_selected_action_row()
        if row >= 0:
            self.run_actions(0, row + 1)

    def run_single(self):
        """Run the currently selected action."""
        row = self.w.get_selected_action_row()
        if row < 0:
            return
        self.run_action_at(row)

    def run_action_at(self, row: int):
        """Run the action at a specific row, ensuring previous actions are complete."""
        if row < 0 or row >= len(self.state.actions):
            return
        if not self.ensure_previous_actions(row):
            return
        self.run_actions(row, row + 1)

    def run_all(self):
        """Run all actions that have not yet been executed."""
        if self.require_data():
            self.run_actions(len(self.state.data_states), len(self.state.actions))

    def run_actions(self, start_idx: int, end_idx: int):
        """Run a contiguous range of pipeline actions."""
        if not self.require_data():
            return

        final_status = "Pipeline complete"
        self.w.set_status("Running pipeline...")
        logger.info("======== Running actions %d to %d ========", start_idx, end_idx)

        if start_idx > 0 and self.state.data_states:
            stored = self.state.data_states[start_idx - 1]
            if stored is None:
                logger.warning(
                    "Checkpoint at index %d is unavailable; falling back to raw_original",
                    start_idx - 1,
                )
            data = stored.copy() if stored is not None else self.state.raw_original.copy()
            del stored
            self.state.data_states.cache.pop(start_idx - 1, None)
        else:
            data = self.state.raw_original.copy()

        # Drop the viz panel's reference to the previous checkpoint
        self.w.viz_panel.current_data = None

        for i in range(start_idx, min(end_idx, len(self.state.actions))):
            action = self.state.actions[i]
            title = get_action_title(action)
            self.w.set_status(f"Running: {title}...")
            logger.info("-------- Running action %d: %s --------", i + 1, title)

            if not self.check_prerequisites(i):
                final_status = "Pipeline stopped (missing prerequisites)"
                break

            action_def = get_action_by_id(action.action_id)
            in_type = action_def.input_type if action_def else DataType.RAW
            out_type = action_def.output_type if action_def else DataType.RAW
            pipeline_type = self.infer_data_type(data)
            logger.debug(
                "Action %d: data class=%s pipeline_type=%s", i + 1, type(data).__name__, pipeline_type
            )

            if in_type == DataType.ANY:
                in_type = pipeline_type
            if out_type == DataType.ANY:
                out_type = pipeline_type

            if in_type != pipeline_type:
                action.status = ActionStatus.ERROR
                action.error_msg = (
                    f"Type mismatch: pipeline produces {pipeline_type.label} data, "
                    f"but this action expects {in_type.label}"
                )
                logger.error(
                    "Type mismatch at action %d: pipeline=%s action_input=%s",
                    i + 1, pipeline_type, in_type,
                )
                self.w.update_action_list(sync_code=False)
                final_status = f"Pipeline stopped: type mismatch at action {i + 1}"
                break

            try:
                call_site, func_defs = self.w.get_execution_code(i, action)
                data = self.execute_action(
                    action, call_site, func_defs, data,
                    input_type=in_type, output_type=out_type,
                )
                self.store_action_result(i, data)
                action.status = ActionStatus.COMPLETE
                if action_def.result_builder_fn:
                    try:
                        action.result = action_def.result_builder_fn(data)
                    except Exception as e:
                        logger.warning("Result builder failed for %s: %s", title, e, exc_info=True)
                self.show_toast(action, title)
                logger.info("Completed action %d: %s", i + 1, title)

            except OperationCancelled:
                action.status = ActionStatus.PENDING
                logger.info("Cancelled while running action %d: %s", i + 1, title)
                final_status = "Pipeline cancelled"
                break

            except Exception as e:
                action.status = ActionStatus.ERROR
                action.error_msg = str(e)
                logger.exception("Action failed at index %d: %s", i, title)
                err_dlg = Adw.AlertDialog(
                    heading="Error",
                    body=f"{title} failed:\n{e}",
                )
                err_dlg.add_response("ok", "OK")
                err_dlg.set_default_response("ok")
                err_dlg.present(self.w.window)
                final_status = f"Pipeline failed at action {i + 1}: {title}"
                break

            self.w.update_action_list()

        self.w.update_visualization()
        self.w.set_status(final_status)
