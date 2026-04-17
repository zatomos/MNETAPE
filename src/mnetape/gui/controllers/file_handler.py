"""File and pipeline persistence handlers for the main window.

FileHandler groups all I/O operations: opening and closing EEG data files, managing the recent files list,
exporting processed data, saving/loading pipeline scripts, auto-saving edits to disk, and reloading when
the open file is modified externally.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QFileDialog, QMessageBox

import mne

from mnetape.core.codegen import extract_custom_preamble, generate_full_script, parse_script_to_actions
from mnetape.core.data_io import load_raw_data, open_file_dialog_filter
from mnetape.core.models import ActionConfig, ActionStatus
from mnetape.gui.controllers.pipeline_runner import OperationCancelled

if TYPE_CHECKING:
    from mnetape.gui.pages.preprocessing_page import PreprocessingPage

logger = logging.getLogger(__name__)


class FileHandler:
    """Handles all file I/O operations for the preprocessing window.

    Operates on PreprocessingPage.state and calls update methods to keep the UI in sync after each operation.
    """

    def __init__(self, window: "PreprocessingPage") -> None:
        self.w = window
        self.state = window.state

    def close_file(self):
        """Close the loaded EEG file and reset state and UI to their initial conditions."""

        self.state.raw_original = None
        self.state.data_states.clear()
        self.state.data_filepath = None
        if self.state.actions and self.state.actions[0].action_id == "load_file":
            self.state.actions[0].params = {"file_path": "", "preload": True}

        for action in self.state.actions:
            action.reset()

        self.w.update_action_list()
        self.w.viz_panel.current_raw = None
        self.w.viz_panel.show_placeholder()
        self.w.viz_panel.status_label.setText("")
        self.w.emit_status("File closed")

    def add_recent_file(self, path: str):
        """Add a file path to the head of the recent-files list and persist it.

        Deduplicates and trims the list to 10 entries.

        Args:
            path: Absolute file path to record.
        """
        if not path:
            return
        path = str(Path(path))
        if path in self.state.recent_fif:
            self.state.recent_fif.remove(path)
        self.state.recent_fif.insert(0, path)
        self.state.recent_fif = self.state.recent_fif[:10]
        self.state.settings.setValue("recent_fif", self.state.recent_fif)

    def refresh_recent_menu(self):
        """No-op: recent menu is rebuilt lazily via MainWindow's aboutToShow signal."""
        pass

    def ensure_load_file_action(self, file_path: str = "") -> None:
        """Ensure state.actions starts with a single load_file action.

        Preserves an existing load_file action (including custom_code/is_custom) and
        moves it to index 0. If none exists, creates a new one.
        """
        existing = next((a for a in self.state.actions if a.action_id == "load_file"), None)
        existing_path = ""
        if existing:
            existing_path = str(existing.params.get("file_path", "") or "")
        resolved_path = file_path or (str(self.state.data_filepath) if self.state.data_filepath else existing_path)

        if existing:
            existing.params = {**existing.params, "file_path": resolved_path, "preload": True}
            existing.reset()
            load_file_action = existing
        else:
            load_file_action = ActionConfig("load_file", {"file_path": resolved_path, "preload": True})

        self.state.actions = [a for a in self.state.actions if a.action_id != "load_file"]
        self.state.actions.insert(0, load_file_action)

    def mark_load_file_complete(self, raw) -> None:
        """Mark the load_file action as complete and store raw in data_states[0].

        Called after data is loaded externally (load_data_path, concatenate) so the UI
        shows load_file as already run without requiring an explicit pipeline execution.
        """
        if not self.state.actions or self.state.actions[0].action_id != "load_file":
            return
        action = self.state.actions[0]
        action.status = ActionStatus.COMPLETE
        if not self.state.data_states:
            self.state.data_states.append(raw.copy())
        else:
            self.state.data_states[0] = raw.copy()

    def apply_stored_montage_if_present(self) -> None:
        """Apply set_montage action params to raw_original in-place, if set_montage is in state.actions.

        Called after loading data when a pipeline with a set_montage step was already parsed.
        Ensures raw_original reflects the montage without needing to run the full pipeline.
        """
        if self.state.raw_original is None:
            return
        montage_action = next(
            (a for a in self.state.actions if a.action_id == "set_montage"), None
        )
        if montage_action is None:
            return
        params = montage_action.params
        renames = params.get("renames")
        montage_name = params.get("montage_name", "")
        montage_file = params.get("montage_file", "")
        try:
            raw = self.state.raw_original
            if renames:
                raw.rename_channels(renames)
            if montage_file:
                if montage_file.lower().endswith(".bvct"):
                    montage = mne.channels.read_dig_captrak(montage_file)
                else:
                    montage = mne.channels.read_custom_montage(montage_file)
            elif montage_name:
                montage = mne.channels.make_standard_montage(montage_name)
            else:
                return
            raw.set_montage(montage, on_missing="warn")
            logger.info("Applied stored montage '%s' to raw_original", montage_name or montage_file)
        except Exception as e:
            logger.warning("Failed to apply stored montage to raw_original: %s", e)

    def load_data_path(self, path: str):
        """Load an EEG file from a known path.

        Resets action states and visualization, updates the status bar, and prompts for a montage if none is found
        in the file.

        Args:
            path: Absolute path to the EEG data file.
        """
        if not path:
            return

        filename = Path(path).name
        try:
            raw = self.w.runner.run_in_thread(
                lambda: load_raw_data(path, preload=True, verbose=False),
                f"Loading {filename}...",
            )
        except OperationCancelled:
            self.w.emit_status("Load cancelled")
            return
        except Exception as e:
            logger.exception("Failed to load data file: %s", path)
            QMessageBox.critical(self.w.window(), "Error", f"Failed to load:\n{e}")
            self.w.emit_status("Load failed")
            return

        self.state.raw_original = raw
        self.state.data_filepath = Path(path)
        self.ensure_load_file_action(path)
        self.state.data_states.clear()

        for action in self.state.actions:
            action.reset()

        self.mark_load_file_complete(raw)
        self.w.update_action_list()
        self.w.update_visualization()

        self.w.emit_status(f"Loaded {self.state.data_filepath.name}")
        self.add_recent_file(path)
        logger.info("Loaded data file: %s", path)

    def open_file(self):
        """Open a file-picker dialog and load the selected EEG file."""
        path, _ = QFileDialog.getOpenFileName(
            self.w.window(), "Open EEG File", "", open_file_dialog_filter()
        )
        if not path:
            return

        self.load_data_path(path)

    def export_file(self, row: int = None):
        """Export the last computed raw object to a FIF file chosen via dialog."""

        # Check for pipeline state
        if row is None:
            if not self.state.data_states:
                QMessageBox.warning(self.w.window(), "No Data", "Run the pipeline first.")
                return
            raw_to_export = self.state.data_states[-1]
        else:
            if row >= len(self.state.data_states):
                QMessageBox.warning(self.w.window(), "No Data", "Selected action has not been computed yet.")
                return
            raw_to_export = self.state.data_states[row]

        path, _ = QFileDialog.getSaveFileName(self.w.window(), "Export Processed", "", "FIF Files (*.fif)")
        if not path:
            return

        if not path.endswith(".fif"):
            path += ".fif"

        try:
            raw_to_export.save(path, overwrite=True)
            self.w.emit_status(f"Exported: {Path(path).name}")
            logger.info("Exported processed FIF: %s", path)
            # In project mode, track this exported file for analysis
            if self.w.project_context:
                ctx = self.w.project_context
                pf = ctx.session.processed_files
                if ctx.run_index is not None and not ctx.session.merge_runs:
                    while len(pf) <= ctx.run_index:
                        pf.append("")
                    pf[ctx.run_index] = path
                elif path not in pf:
                    pf.append(path)
                ctx.project.save(ctx.project_dir)
        except Exception as e:
            logger.exception("Export failed: %s", path)
            QMessageBox.critical(self.w.window(), "Error", f"Export failed:\n{e}")

    def new_pipeline(self):
        """Reset pipeline steps while preserving the required load_file action."""
        self.state.push_undo()
        self.w.mark_pipeline_dirty()
        self.state.actions = []
        self.ensure_load_file_action()
        self.state.data_states.clear()
        self.w.update_action_list()
        self.w.update_visualization()
        self.w.emit_status("New pipeline")

    def save_pipeline_default(self) -> bool:
        """Save pipeline to the participant path (project mode) or current file (standalone).

        Falls back to a Save As dialog if no file is currently open.
        Returns True on success or if there was nothing to save, False if canceled or failed.
        """
        code = self.w.code_panel.get_code()
        if not code:
            return True
        if self.w.project_context:
            ctx = self.w.project_context
            fp = ctx.project.participant_pipeline_path(ctx.project_dir, ctx.participant, ctx.session)
            fp.parent.mkdir(parents=True, exist_ok=True)
        else:
            fp = self.state.pipeline_filepath
            if not fp:
                return self.save_pipeline()
        try:
            fp.write_text(code)
            self.w.code_panel.file_hash = hashlib.md5(code.encode()).hexdigest()
            self.state.pipeline_filepath = fp
            self.w.clear_pipeline_dirty()
            self.w.code_panel.set_file(fp)
            self.w.emit_status(f"Saved: {fp.name}", 2000)
            return True
        except Exception as exc:
            logger.exception("Failed to save pipeline")
            QMessageBox.critical(self.w.window(), "Save Failed", f"Could not save pipeline:\n{exc}")
            return False

    def save_pipeline(self) -> bool:
        """Serialize the current action list to a Python script via a Save As dialog.

        Returns True on success, False if cancelled or failed.
        """
        path, _ = QFileDialog.getSaveFileName(self.w.window(), "Save Pipeline As", "", "Python Files (*.py)")
        if not path:
            return False

        if not path.endswith(".py"):
            path += ".py"

        try:
            code = generate_full_script(self.state.actions, extra_preamble=self.state.custom_preamble or None)
            Path(path).write_text(code)
        except Exception as exc:
            logger.exception("Failed to save pipeline")
            QMessageBox.critical(self.w.window(), "Save Failed", f"Could not save pipeline:\n{exc}")
            return False

        self.state.pipeline_filepath = Path(path)
        self.state.pipeline_dirty = False
        self.w.code_panel.set_file(self.state.pipeline_filepath)
        self.w.emit_status(f"Saved: {self.state.pipeline_filepath.name}")
        return True

    def load_pipeline(self):
        """Open a Python pipeline script and parse it back into actions."""
        path, _ = QFileDialog.getOpenFileName(self.w.window(), "Load Pipeline", "", "Python Files (*.py)")
        if not path:
            return

        try:
            code = Path(path).read_text()
            self.state.actions = parse_script_to_actions(code)
            self.state.custom_preamble = extract_custom_preamble(code, self.state.actions)
            self.state.pipeline_filepath = Path(path)
            self.w.clear_pipeline_dirty()
            self.w.code_panel.set_file(self.state.pipeline_filepath)
            self.state.data_states.clear()

            self.w.update_action_list()
            self.w.update_visualization()
            self.w.emit_status(f"Loaded pipeline: {self.state.pipeline_filepath.name}")
            logger.info("Loaded pipeline script: %s", path)
        except Exception as e:
            logger.exception("Failed to load pipeline: %s", path)
            QMessageBox.critical(self.w, "Error", f"Failed to load pipeline:\n{e}")

    def reload_pipeline(self):
        """Reload the pipeline from the currently open file, discarding computed states."""
        if not self.state.pipeline_filepath or not self.state.pipeline_filepath.exists():
            return
        try:
            code = self.state.pipeline_filepath.read_text()
            actions = parse_script_to_actions(code)
        except Exception as exc:
            logger.exception("Failed to reload pipeline from %s", self.state.pipeline_filepath)
            QMessageBox.warning(
                self.w, "Reload Failed",
                f"Could not reload pipeline:\n{exc}\n\nThe current session state is unchanged.",
            )
            return
        self.state.actions = actions
        self.state.custom_preamble = extract_custom_preamble(code, actions)
        self.state.data_states.clear()
        self.w.update_action_list(sync_code=False)
        self.w.code_panel.set_code(code)
        self.w.emit_status("Reloaded from file")

    def on_external_code_change(self):
        """Handle the pipeline file being modified externally."""
        self.reload_pipeline()
        self.w.emit_status("Reloaded from disk", 3000)
        self.w.code_panel.pending_external_change = False
        logger.info("Auto-reloaded pipeline after external file change")
