"""Pipeline execution for the main window.

PipelineRunner orchestrates running actions, manages QThread workers for non-interactive processing,
and handles cancellation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import mne

if TYPE_CHECKING:
    from typing import Any, Callable

_T = TypeVar("_T")

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.executor import exec_action
from mnetape.core.models import ActionResult, ActionStatus, DataType, ICASolution
from mnetape.gui.widgets.toast_notification import ToastNotification

if TYPE_CHECKING:
    from mnetape.gui.pages.preprocessing_page import PreprocessingPage

logger = logging.getLogger(__name__)


class OperationCancelled(Exception):
    """Raised when a long-running operation is canceled by the user."""


class PipelineRunner:
    """Orchestrates action execution for the main window.

    All heavy processing runs inside a QThread via run_in_thread().
    """

    def __init__(self, window: "PreprocessingPage") -> None:
        self.w = window
        self.state = window.state
        self.current_toast = None
        self.last_warnings: list[str] = []

    # -------- Helpers --------

    def show_toast(self, action, title: str, warnings: list[str] | None = None) -> None:
        """Show a toast notification for a completed action, replacing any existing one."""
        if self.current_toast is not None:
            self.current_toast.close()
        on_results = None
        if isinstance(action.result, ActionResult):
            on_results = lambda res=action.result, t=title: self.w.show_action_result(res, t)
        elif action.result is not None:
            logger.warning("Unexpected action.result type: %s (value=%r)",
                           type(action.result).__name__, action.result)
        toast_parent = self.w.window()
        toast = ToastNotification(
            f'"{title}" complete',
            parent=toast_parent,
            on_view_results=on_results,
            warnings=warnings or None,
        )
        toast.destroyed.connect(lambda: setattr(self, "current_toast", None))
        self.current_toast = toast
        toast.show()

    def require_data(self) -> bool:
        """Show a warning and return False when no EEG file is loaded."""
        if self.state.raw_original is None:
            QMessageBox.warning(self.w, "No Data", "Load a FIF file first.")
            return False
        return True

    def get_data_type_at(self, row: int) -> DataType:
        """Return the DataType flowing into the action at row.

        Walks through all preceding actions to compute the cumulative output type.

        Args:
            row: Index of the action to check.

        Returns:
            The DataType the preceding action outputs. Fallbacks to DataType.RAW.
        """
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
        """Return a copy of the data object to pass into the action at row.

        Reads from data_states[row-1] when available, then falls back to raw_original.

        Args:
            row: Index of the action in the pipeline list.

        Returns:
            A copy of the appropriate data object.
        """
        if 0 < row <= len(self.state.data_states):
            stored = self.state.data_states[row - 1]
            if stored is not None:
                return stored.copy()
        return self.state.raw_original.copy()

    def store_action_result(self, row, data):
        """Store the processed data object at the given pipeline position.

        Pads data_states with copies of raw_original (for RAW positions) or None
        if needed so the list is contiguous up to row.

        Args:
            row: Index where the result should be stored.
            data: The processed data object to store.
        """
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
        completed_ids = {
            a.action_id for a in preceding if a.status == ActionStatus.COMPLETE
        }

        warnings: list[str] = []
        for prereq in action_def.prerequisites:
            if prereq.action_id not in completed_ids:
                warnings.append(prereq.message)

        if not warnings:
            return True

        text = "\n".join(f"• {w}" for w in warnings)
        reply = QMessageBox.warning(
            self.w,
            f"Missing Prerequisites for {action_def.title}",
            f"{text}\n\nContinue anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def ensure_previous_actions(self, row) -> bool:
        """Ensure that all actions before row have been executed."""
        if row <= 0 or row <= len(self.state.data_states):
            return True
        reply = QMessageBox.question(
            self.w, "Run Previous?",
            "Previous actions haven't been run. Run them first?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.run_actions(len(self.state.data_states), row)
        return row <= len(self.state.data_states)

    def run_in_thread(self, fn: Callable[[], _T], message: str = "Processing...") -> _T:
        """Execute a callable in a background QThread with a cancellable progress dialog."""
        result: list[Any] = [None]
        error: list[BaseException | None] = [None]
        cancel_requested = [False]

        class _Worker(QThread):
            done = pyqtSignal()

            def run(self):
                try:
                    if self.isInterruptionRequested():
                        return
                    result[0] = fn()
                except BaseException as e:
                    error[0] = e
                finally:
                    self.done.emit()

        progress = QProgressDialog(message, "Cancel", 0, 0, self.w)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setFixedSize(350, 100)
        progress.setValue(0)
        progress.show()

        worker = _Worker()
        worker.start()
        while worker.isRunning():
            QApplication.processEvents()
            if progress.wasCanceled() and not cancel_requested[0]:
                cancel_requested[0] = True
                self.w.emit_status("Cancelling...")
                progress.setCancelButtonText("Cancelling...")
                progress.setCancelButton(None)
                worker.requestInterruption()
            if cancel_requested[0]:
                break
            worker.wait(25)
        progress.close()

        if cancel_requested[0]:
            if not worker.wait(500):
                logger.warning("Worker thread still running after cancel; it will complete in background")
            self.w.emit_status("Operation cancelled.")
            raise OperationCancelled("Operation cancelled.")
        if worker.isRunning() and not worker.wait(200):
            raise RuntimeError("Background worker did not finish cleanly.")
        if error[0]:
            raise error[0]
        return result[0]

    def execute_action(self, action, action_def, call_site: str, func_defs: str, data,
                       input_type: DataType = DataType.RAW, output_type: DataType = DataType.RAW):
        """Execute a single action and return the resulting data object.

        Actions are run in a background QThread.

        Args:
            action: The ActionConfig being executed.
            action_def: The ActionDefinition for the action.
            call_site: Call-site Python statement (or custom code block).
            func_defs: Function definition(s) to exec before the call site.
            data: Input data object.
            input_type: DataType of the incoming data.
            output_type: DataType of the expected result.

        Returns:
            The new data object produced by the action.

        Raises:
            OperationCancelled: When the user cancels a threaded action.
        """
        import warnings
        caught: list = []

        def fn():
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = exec_action(call_site, func_defs, data, action, input_type=input_type, output_type=output_type)
            caught.extend(w)
            return result

        title = get_action_title(action)
        result = self.run_in_thread(fn, f"Running: {title}...")
        self.last_warnings = list(dict.fromkeys(
            str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)
        ))
        for msg in self.last_warnings:
            logger.warning("RuntimeWarning from '%s': %s", title, msg)
        return result

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
        first_action = self.state.actions[0] if self.state.actions else None
        starts_with_load_file = (
            first_action is not None and first_action.action_id == "load_file"
        )
        if starts_with_load_file:
            fp = first_action.params.get("file_path", "")
            if not fp:
                QMessageBox.warning(self.w, "No File Path",
                                    "No file path set in the Load File action. Edit it first.")
                return
        elif not self.require_data():
            return

        # Warn if any upcoming action requires manual inspection
        start = len(self.state.data_states)
        for i in range(start, len(self.state.actions)):
            action = self.state.actions[i]
            action_def = get_action_by_id(action.action_id)
            if not action_def or not action_def.interactive_runner:
                continue
            ir = action_def.interactive_runner
            if not ir.needs_inspection or not ir.needs_inspection(action):
                continue
            title = get_action_title(action)
            reply = QMessageBox.warning(
                self.w,
                "Manual Inspection Required",
                f'"{title}" (step {i + 1}) requires manual component inspection.\n\n'
                "The inspection dialog will open during execution. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            break   # warn once

        self.run_actions(len(self.state.data_states), len(self.state.actions))

    def run_actions(self, start_idx, end_idx):
        """Run a contiguous range of pipeline actions.

        Executes actions from start_idx up to (but not including) end_idx,
        accumulating data states and updating the UI after each action.

        Args:
            start_idx: Index of the first action to run.
            end_idx: Index one past the last action to run.
        """
        final_status = "Pipeline complete"
        self.w.emit_status("Running pipeline...")
        logger.info("======== Running actions %d to %d ========", start_idx, end_idx)

        if start_idx > 0 and self.state.data_states:
            stored = self.state.data_states[start_idx - 1]
            if stored is None:
                logger.warning(
                    "Checkpoint at index %d is unavailable; falling back to raw_original", start_idx - 1
                )
            data = stored.copy() if stored is not None else self.state.raw_original.copy()
            del stored  # release the DataStore reference, only copy is needed
            # Pop from LRU cache
            self.state.data_states.cache.pop(start_idx - 1, None)
        elif self.state.raw_original is not None:
            data = self.state.raw_original.copy()
        else:
            data = None  # load_file at index 0 will populate this

        QApplication.processEvents()

        # Drop the viz panel's reference to the previous checkpoint so it can be freed
        self.w.viz_panel.current_data = None

        for i in range(start_idx, min(end_idx, len(self.state.actions))):
            action = self.state.actions[i]
            title = get_action_title(action)
            self.w.emit_status(f"Running: {title}...")
            logger.info("-------- Running action %d: %s --------", i + 1, title)
            QApplication.processEvents()

            if not self.check_prerequisites(i):
                final_status = "Pipeline stopped (missing prerequisites)"
                break

            action_def = get_action_by_id(action.action_id)
            in_type = action_def.input_type if action_def else DataType.RAW
            out_type = action_def.output_type if action_def else DataType.RAW

            # Skip type-mismatch check when data is None
            is_load_file = action.action_id == "load_file"
            if not is_load_file:
                if data is None:
                    action.status = ActionStatus.ERROR
                    action.error_msg = "No input data available."
                    self.w.update_action_list(sync_code=False)
                    final_status = f"Pipeline stopped: no data for action {i + 1}"
                    break

                pipeline_type = self.infer_data_type(data)
                logger.debug("Action %d: data class=%s pipeline_type=%s",
                             i + 1, type(data).__name__, pipeline_type)
                # For ANY actions, use the actual pipeline type for execution
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
                    logger.error("Type mismatch at action %d: pipeline=%s action_input=%s",
                                 i + 1, pipeline_type, in_type)
                    self.w.update_action_list(sync_code=False)
                    final_status = f"Pipeline stopped: type mismatch at action {i + 1}"
                    break

            # Interactive runner hook
            if action_def and action_def.interactive_runner:
                try:
                    data = action_def.interactive_runner.run(action, data, self.w)
                except OperationCancelled:
                    action.status = ActionStatus.PENDING
                    final_status = "Pipeline cancelled"
                    break
                except Exception as e:
                    action.status = ActionStatus.ERROR
                    action.error_msg = str(e)
                    logger.exception("Interactive runner failed at index %d: %s", i, title)
                    QMessageBox.critical(self.w, "Error", f"{title} failed:\n{e}")
                    final_status = f"Pipeline failed at action {i + 1}: {title}"
                    break

            try:
                call_site, func_defs = self.w.get_execution_code(i, action)
                data = self.execute_action(action, action_def, call_site, func_defs, data,
                                           input_type=in_type, output_type=out_type)
                if is_load_file:
                    # Sync state so subsequent actions and visualization see the loaded data
                    self.state.raw_original = data
                    fp = action.params.get("file_path", "")
                    if fp:
                        self.state.data_filepath = Path(fp)
                    self.w.files.apply_stored_montage_if_present()
                self.store_action_result(i, data)
                action.status = ActionStatus.COMPLETE
                if action_def.result_builder_fn:
                    try:
                        action.result = action_def.result_builder_fn(data)
                    except Exception as e:
                        logger.warning("Result builder failed for %s: %s", title, e, exc_info=True)
                self.show_toast(action, title, warnings=self.last_warnings or None)
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
                QMessageBox.critical(self.w, "Error", f"{title} failed:\n{e}")
                final_status = f"Pipeline failed at action {i + 1}: {title}"
                break

            self.w.update_action_list()

        self.w.viz_panel.current_step = min(end_idx, len(self.state.data_states))
        self.w.update_visualization()
        self.w.emit_status(final_status)

        # Notify project context when the full pipeline completes without error
        if (
            final_status == "Pipeline complete"
            and end_idx >= len(self.state.actions)
            and hasattr(self.w, "on_pipeline_complete")
        ):
            self.w.on_pipeline_complete()
