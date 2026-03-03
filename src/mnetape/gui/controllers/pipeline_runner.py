"""Pipeline and step execution for the main window.

PipelineRunner orchestrates running actions, manages QThread workers for non-interactive processing,
handles cancellation, and provides helper methods for prerequisite checking and step-block extraction.
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from mnetape.actions.registry import get_action_by_id, get_action_title
from mnetape.core.codegen import extract_step_blocks
from mnetape.core.executor import exec_action_code, SCOPE_VAR
from mnetape.core.models import ActionStatus, DataType

if TYPE_CHECKING:
    from mnetape.gui.controllers.main_window import MainWindow

logger = logging.getLogger(__name__)


class OperationCancelled(Exception):
    """Raised when a long-running operation is canceled by the user.

    Used to propagate cancellation from the QThread worker through to
    the outer run_actions / run_step_range loop.
    """


def get_step_blocks(action_def, action, code: str) -> list[dict]:
    """Extract step blocks from code, regenerating from action params when markers are absent.

    Args:
        action_def: The ActionDefinition for the action, or None.
        action: The ActionConfig instance (provides fallback params).
        code: The Python source code to search for step markers.

    Returns:
        List of step-block dicts (keys: id, title, code), or an empty list when the action has no steps,
        or no markers are found and regeneration also produces no markers.
    """

    if not action_def or not action_def.has_steps():
        return []
    step_blocks = extract_step_blocks(code)
    if step_blocks:
        return step_blocks
    params = action_def.default_params()
    params.update(action.params)
    return extract_step_blocks(action_def.build_code(params))


class PipelineRunner:
    """Orchestrates action and step execution for the main window.

    All heavy processing runs inside a QThread via run_in_thread(). Interactive steps run on the main Qt thread via
    their interactive_runner callable.
    """

    def __init__(self, window: MainWindow) -> None:
        self.w = window
        self.state = window.state


    # -------- Helpers --------

    def require_data(self) -> bool:
        """Show a warning and return False when no EEG file is loaded."""
        if self.state.raw_original is None:
            QMessageBox.warning(self.w, "No Data", "Load a FIF file first.")
            return False
        return True

    def get_data_type_at(self, row: int) -> DataType:
        """Return the DataType flowing into the action at row.

        Args:
            row: Index of the action to check .

        Returns:
            The DataType the preceding action outputs. Fallbacks to DataType.RAW.
        """
        for action in self.state.actions[:row]:
            action_def = get_action_by_id(action.action_id)
            if action_def:
                return action_def.output_type
        return DataType.RAW

    def get_step_input(self, action, row):
        """Return a copy of the correct data object to pass into the next step.

        Priority: data from previous step scope (for multistep continuations), then data_states[row-1],
        then raw_original (for RAW type only).

        Args:
            action: ActionConfig whose step_state may contain prior-step data.
            row: Index of the action in the pipeline list.

        Returns:
            A copy of the MNE object.
        """
        input_type = self.get_data_type_at(row)
        scope_var = SCOPE_VAR[input_type]
        if action.completed_steps > 0:
            scope_data = action.step_state.get("scope", {}).get(scope_var)
            if scope_data is not None:
                return scope_data.copy()
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
            data: The processed MNE object to store.
        """
        while len(self.state.data_states) < row:
            if self.get_data_type_at(len(self.state.data_states)) == DataType.RAW:
                self.state.data_states.append(self.state.raw_original.copy())
            else:
                self.state.data_states.append(None)
        if row < len(self.state.data_states):
            self.state.data_states[row] = data
        else:
            self.state.data_states.append(data)

    def check_prerequisites(self, action_idx: int) -> bool:
        """Check if all prerequisite actions have been run, prompting if not.

        Args:
            action_idx: Index of the action being checked.

        Returns:
            True when all prerequisites are satisfied, or when the user chooses to proceed despite unmet prerequisites.
        """

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
        """Ensure that all actions before row have been executed.

        If the preceding actions have not been run, prompts the user and optionally runs them before proceeding.

        Args:
            row: Index of the action to check prerequisites for.

        Returns:
            True when previous actions are complete and execution can proceed.
        """
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

    def run_in_thread(self, fn, message="Processing..."):
        """Execute a callable in a background QThread with a cancellable progress dialog.

        Displays a modal QProgressDialog while the thread runs. The user can click Cancel to request an interruption.
        MNE operations are not truly interruptible, so the thread may finish in the background after cancellation is
        acknowledged.

        Args:
            fn: Callable to run in the worker thread.
            message: Text shown in the progress dialog. Defaults to "Processing...".

        Returns:
            The return value of fn.

        Raises:
            OperationCancelled: When the user clicks Cancel.
            RuntimeError: When the worker thread does not finish cleanly.
            Exception: Re-raises any exception thrown by fn.
        """
        result: list[object | None] = [None]
        error: list[Exception | None] = [None]
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

        # Create a modal progress dialog that allows cancellation
        progress = QProgressDialog(message, "Cancel", 0, 0, self.w)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        progress.setFixedSize(350, 100)
        progress.setValue(0)
        progress.show()

        # Start the worker thread
        worker = _Worker()
        worker.start()
        while worker.isRunning():
            QApplication.processEvents()
            if progress.wasCanceled() and not cancel_requested[0]:
                cancel_requested[0] = True
                self.w.status.showMessage("Cancelling...")
                progress.setCancelButtonText("Cancelling...")
                progress.setCancelButton(None)
                worker.requestInterruption()
            if cancel_requested[0]:
                break
            worker.wait(25)
        progress.close()

        if cancel_requested[0]:
            # Give the thread a short window to notice the interruption before we move on.
            # MNE operations are not interruptible, so the thread may continue in background.
            if not worker.wait(500):
                logger.warning("Worker thread still running after cancel; it will complete in background")
            self.w.status.showMessage("Operation cancelled.")
            raise OperationCancelled("Operation cancelled.")
        if worker.isRunning() and not worker.wait(200):
            raise RuntimeError("Background worker did not finish cleanly.")
        if error[0]:
            raise error[0]
        return result[0]

    def execute_step(self, action, action_def, step, code, data,
                     input_type: DataType = DataType.RAW, output_type: DataType = DataType.RAW):
        """Execute a single step and return the resulting data object.

        Interactive steps are run synchronously on the main thread via their interactive_runner.
        Non-interactive steps are run in a background QThread.

        Args:
            action: The ActionConfig being executed.
            action_def: The ActionDefinition for the action.
            step: The StepDefinition, or None for single-step actions.
            code: Python source code for this step.
            data: Input data object for this step.
            input_type: DataType of the incoming data.
            output_type: DataType of the expected result.

        Returns:
            The new data object produced by the step.

        Raises:
            RuntimeError: For interactive steps with no runner, or when the user closes an interactive dialog
                without applying.
            OperationCancelled: When the user cancels a threaded step.
        """

        if step and step.interactive:
            if not step.interactive_runner:
                raise RuntimeError("No interactive runner configured.")
            result = step.interactive_runner(action, data, parent=self.w)
            if result is None:
                raise RuntimeError("Interactive step cancelled.")
            return result
        title = step.title if step else get_action_title(action)
        return self.run_in_thread(
            lambda c=code, d=data: exec_action_code(
                c, d, action, reuse_scope=step is not None,
                input_type=input_type, output_type=output_type,
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
        """Run the action at a specific row, ensuring previous actions are complete.

        Args:
            row:  index of the action to run.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        if not self.ensure_previous_actions(row):
            return
        self.run_actions(row, row + 1)

    def run_all(self):
        """Run all actions that have not yet been executed."""
        if self.require_data():
            self.run_actions(len(self.state.data_states), len(self.state.actions))

    def run_actions(self, start_idx, end_idx):
        """Run a contiguous range of pipeline actions.

        Executes actions from start_idx up to (but not including) end_idx,
        accumulating raw states and updating the UI after each action.

        Args:
            start_idx: Index of the first action to run.
            end_idx: Index one past the last action to run.
        """
        if not self.require_data():
            return

        final_status = "Pipeline complete"
        self.w.status.showMessage("Running pipeline...")
        logger.info("======== Running actions %d to %d ========", start_idx, end_idx)
        QApplication.processEvents()

        if start_idx > 0 and self.state.data_states:
            stored = self.state.data_states[start_idx - 1]
            data = stored.copy() if stored is not None else self.state.raw_original.copy()
        else:
            data = self.state.raw_original.copy()

        for i in range(start_idx, min(end_idx, len(self.state.actions))):
            action = self.state.actions[i]
            title = get_action_title(action)
            self.w.status.showMessage(f"Running: {title}...")
            logger.info("-------- Running action %d: %s --------", i + 1, title)
            QApplication.processEvents()

            if not self.check_prerequisites(i):
                final_status = "Pipeline stopped (missing prerequisites)"
                break

            action_def = get_action_by_id(action.action_id)
            in_type = action_def.input_type if action_def else DataType.RAW
            pipeline_type = self.get_data_type_at(i)
            if in_type != pipeline_type:
                action.status = ActionStatus.ERROR
                action.error_msg = (
                    f"Type mismatch: pipeline produces {pipeline_type.value} data, "
                    f"but this action expects {in_type.value}"
                )
                logger.error("Type mismatch at action %d: pipeline=%s action_input=%s", i + 1, pipeline_type, in_type)
                self.w.update_action_list(sync_code=False)
                final_status = f"Pipeline stopped: type mismatch at action {i + 1}"
                break

            try:
                action.step_state = {}
                action.completed_steps = 0

                out_type = action_def.output_type if action_def else DataType.RAW

                code = self.w.get_action_code(i, action)
                step_blocks = get_step_blocks(action_def, action, code)

                if step_blocks:
                    for step_idx, block in enumerate(step_blocks):
                        step = action_def.steps[step_idx] if step_idx < len(action_def.steps) else None
                        data = self.execute_step(action, action_def, step, block["code"], data,
                                                 input_type=in_type, output_type=out_type)
                        action.completed_steps = step_idx + 1
                        self.w.update_action_list(sync_code=False)
                else:
                    data = self.execute_step(action, action_def, None, code, data,
                                             input_type=in_type, output_type=out_type)
                    if action_def and action_def.steps:
                        action.completed_steps = len(action_def.steps)

                self.store_action_result(i, data)
                action.status = ActionStatus.COMPLETE
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

        self.w.viz_panel.step_combo.setCurrentIndex(min(end_idx, len(self.state.data_states)))
        self.w.update_visualization()
        self.w.status.showMessage(final_status)


    # -------- Step-level execution --------

    def run_single_step(self, row):
        """Run the next pending step of a multistep action.

        Args:
            row:  index of the action in the pipeline list.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        action_def = get_action_by_id(action.action_id)
        if not action_def or not action_def.has_steps():
            return
        step_idx = action.completed_steps
        if step_idx >= len(action_def.steps):
            return
        self.run_step_range(row, step_idx, step_idx + 1)

    def run_remaining_steps(self, row):
        """Run all remaining steps of a multistep action starting from the next pending one.

        Args:
            row:  index of the action in the pipeline list.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        action = self.state.actions[row]
        action_def = get_action_by_id(action.action_id)
        if not action_def or not action_def.has_steps():
            return
        self.run_step_range(row, action.completed_steps, len(action_def.steps))

    def run_step_range(self, row, start_step, end_step):
        """Run a contiguous range of steps for a single multistep action.

        Args:
            row: Index of the action in the pipeline list.
            start_step: Index of the first step to run.
            end_step: Index one past the last step to run.
        """
        if not self.require_data():
            return
        if not self.ensure_previous_actions(row):
            return
        if start_step == 0 and not self.check_prerequisites(row):
            return

        action = self.state.actions[row]
        action_def = get_action_by_id(action.action_id)
        in_type = action_def.input_type if action_def else DataType.RAW
        out_type = action_def.output_type if action_def else DataType.RAW
        data = self.get_step_input(action, row)
        code = self.w.get_action_code(row, action)
        step_blocks = get_step_blocks(action_def, action, code)

        run_as_single = not step_blocks
        if run_as_single:
            if start_step > 0:
                QMessageBox.warning(self.w, "No Step Markers",
                    "No step markers in code. Run the full action instead.")
                return
            step_blocks = [{"code": code, "title": get_action_title(action)}]

        end_step = min(end_step, len(step_blocks))

        try:
            # Run steps
            for step_idx in range(start_step, end_step):
                step = action_def.steps[step_idx] if step_idx < len(action_def.steps) else None
                title = step.title if step else f"Step {step_idx + 1}"
                self.w.status.showMessage(f"Running: {title}...")
                QApplication.processEvents()
                data = self.execute_step(action, action_def, step, step_blocks[step_idx]["code"], data,
                                         input_type=in_type, output_type=out_type)
                action.completed_steps = step_idx + 1
                if action.status == ActionStatus.ERROR:
                    action.status = ActionStatus.PENDING
                self.w.update_action_list(sync_code=False)

            if run_as_single:
                action.completed_steps = len(action_def.steps)

            done = action.completed_steps >= len(action_def.steps)
            if done:
                action.status = ActionStatus.COMPLETE
                self.store_action_result(row, data)
            self.w.update_action_list()
            if done:
                self.w.update_visualization()
            self.w.status.showMessage("Steps complete")
        except OperationCancelled:
            if action.status == ActionStatus.ERROR:
                action.status = ActionStatus.PENDING
            self.w.update_action_list(sync_code=False)
            self.w.status.showMessage("Operation cancelled.")
        except Exception as e:
            action.status = ActionStatus.ERROR
            action.error_msg = str(e)
            self.w.update_action_list()
            logger.exception("Step failed for action_id=%s row=%d", action.action_id, row)
            QMessageBox.critical(self.w, "Error", f"Step failed:\n{e}")

    def reset_action_steps(self, row):
        """Reset step progress for the action at row and invalidate all downstream raw states.

        Args:
            row:  index of the action to reset.
        """
        if row < 0 or row >= len(self.state.actions):
            return
        self.state.actions[row].reset()
        self.state.data_states = self.state.data_states[:row]
        for a in self.state.actions[row + 1:]:
            if not a.is_custom:
                a.reset()
        self.w.update_action_list()
